import asyncio
import fcntl
import hmac
import logging
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .auth import Auth
from .chat import ROLES, Chat
from .codex import AppServer, CodexError
from .config import Settings
from .db import Database
from .orchestration import Planner, Scheduler, Verifier
from .policies import Policies
from .registry import Registry, git
from .tasks import Tasks
from .workers import Workers
from .workspace import safe_file


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=1024)


class SetupBody(LoginBody):
    token: str = Field(min_length=20, max_length=100)


class ProjectBody(BaseModel):
    path: str = Field(max_length=4096)
    name: str | None = Field(default=None, max_length=200)


class ProjectUpdate(BaseModel):
    revision: int
    display_name: str | None = Field(default=None, max_length=80)
    description: str | None = Field(default=None, max_length=5000)
    objective: str | None = Field(default=None, max_length=5000)
    success_criteria: str | None = Field(default=None, max_length=5000)
    instructions: str | None = Field(default=None, max_length=10000)
    priority: int | None = Field(default=None, ge=0, le=100)
    status: str | None = None


class MemoryBody(BaseModel):
    revision: int
    content: str = Field(max_length=100000)


class TaskBody(BaseModel):
    project_id: str
    description: str = Field(min_length=1, max_length=10000)
    acceptance: str = Field(min_length=1, max_length=10000)
    priority: int = Field(default=50, ge=0, le=100)
    dependencies: list[str] = Field(default_factory=list, max_length=50)
    verification: str = Field(default="", max_length=5000)
    permissions: list[str] = Field(default_factory=list, max_length=30)


class MessageBody(BaseModel):
    project_id: str | None = None
    content: str = Field(min_length=1, max_length=24000)
    mode: Literal["auto", "conversation", "execute"] = "auto"


class RouteBody(BaseModel):
    model: str
    effort: str


class InstructionsBody(BaseModel):
    content: str = Field(max_length=10000)


class TaskControl(BaseModel):
    revision: int
    status: str


class ResumeBody(BaseModel):
    revision: int
    reconciled: bool = False
    reconciliation: str = Field(default="", max_length=3000)


class GoalBody(BaseModel):
    project_id: str
    goal: str = Field(min_length=1, max_length=10000)
    request_key: str = Field(min_length=8, max_length=100)


class SchedulerBody(BaseModel):
    enabled: bool
    project_id: str | None = None
    limit: int | None = Field(default=None, ge=1, le=100)
    max_workers: int = Field(default=2, ge=1, le=8)


class VerificationCommands(BaseModel):
    commands: list[list[str]] = Field(max_length=10)


class PolicyBody(BaseModel):
    mode: str | None
    risk_accepted: bool = False


class ApprovalBody(BaseModel):
    approve: bool


class NotificationSettings(BaseModel):
    daily_summaries: bool


def create_app(settings: Settings | None = None, server=None):
    settings = settings or Settings()
    settings.prepare()
    db = Database(settings.db_path)
    registry, tasks, auth = Registry(db, settings.project_roots), Tasks(db), Auth(db, settings)
    chat = Chat(db, registry, server or AppServer(settings))
    workers = Workers(db, registry, settings)
    planner, verifier = Planner(db, registry, settings), Verifier(db, registry, settings)
    scheduler = Scheduler(db, registry, settings, workers, verifier, planner)
    chat.action_handler = scheduler.manager_action
    chat.receipt_provider = scheduler.receipts
    policies = Policies(db)

    @asynccontextmanager
    async def lifespan(app):
        lock_file = (settings.data_dir / "manager.lock").open("a")
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            lock_file.close()
            raise RuntimeError("Another manager instance owns this data directory")
        auth.bootstrap()
        chat.recover()
        workers.recover()
        scheduler.recover()
        registry.discover()
        for project in registry.list():
            tasks.sync(project["id"])
        with db.transaction() as conn:
            conn.execute("UPDATE approvals SET status='cancelled' WHERE status IN ('pending','approved')")
        settings.max_workers = db.setting("max_workers", settings.max_workers)
        scheduler.start()
        try:
            yield
        finally:
            await scheduler.close()
            await workers.close()
            await chat.close()
            lock_file.close()

    app = FastAPI(title="AI Manager", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.db, app.state.registry, app.state.chat, app.state.auth = db, registry, chat, auth
    app.state.workers = workers
    app.state.scheduler = scheduler
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts))

    @app.middleware("http")
    async def browser_security(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).netloc != request.headers.get("host"):
                return JSONResponse({"detail": "Origin rejected"}, status_code=403)
            if request.url.path not in {"/api/login", "/api/setup"}:
                session = auth.session(request.cookies.get("ai_manager_session"))
                if not session:
                    return JSONResponse({"detail": "Login required"}, status_code=401)
                if not hmac.compare_digest(session["csrf"], request.headers.get("x-csrf-token", "")):
                    return JSONResponse({"detail": "CSRF token required"}, status_code=403)
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    def require_owner(request: Request):
        session = auth.session(request.cookies.get("ai_manager_session"))
        if not session:
            raise HTTPException(401, "Login required")
        return session

    @app.exception_handler(ValueError)
    async def invalid(request, error):
        logging.getLogger(__name__).warning("action.rejected path=%s reason=%s", request.url.path, str(error))
        return JSONResponse({"result": "rejected", "detail": str(error), "message": str(error)}, status_code=409)

    @app.exception_handler(CodexError)
    async def unavailable(request, error):
        logging.getLogger(__name__).error("action.failed path=%s reason=%s", request.url.path, str(error))
        return JSONResponse({"result": "failed", "detail": str(error), "message": str(error)}, status_code=503)

    @app.get("/api/auth-status")
    def auth_status(request: Request):
        session = auth.session(request.cookies.get("ai_manager_session"))
        return {
            "setup_required": not bool(db.one("SELECT id FROM owner")),
            "authenticated": bool(session),
            "csrf": session["csrf"] if session else None,
        }

    @app.post("/api/setup")
    async def setup(body: SetupBody, request: Request):
        # Bootstrap token checks are also throttled, with no remote password reset path.
        import time

        ip = request.client.host if request.client else "unknown"
        with db.transaction() as conn:
            conn.execute("DELETE FROM login_attempts WHERE at < ?", (time.time() - 900,))
            if conn.execute("SELECT count(*) FROM login_attempts").fetchone()[0] >= 10:
                raise HTTPException(429, "Too many setup attempts")
            conn.execute("INSERT INTO login_attempts VALUES(?,?)", (ip, time.time()))
        await asyncio.to_thread(auth.setup, body.username, body.password, body.token)
        return {"created": True}

    @app.post("/api/login")
    async def login(body: LoginBody, request: Request):
        try:
            token, csrf = await asyncio.to_thread(
                auth.login, body.username, body.password, request.client.host if request.client else "unknown"
            )
        except ValueError as error:
            raise HTTPException(429 if "Too many" in str(error) else 401, str(error)) from error
        response = JSONResponse({"csrf": csrf})
        response.set_cookie(
            "ai_manager_session",
            token,
            httponly=True,
            secure=settings.secure_cookie,
            samesite="strict",
            max_age=settings.session_seconds,
            path="/",
        )
        return response

    @app.post("/api/logout", dependencies=[Depends(require_owner)])
    def logout(request: Request):
        auth.logout(request.cookies.get("ai_manager_session"))
        response = JSONResponse({"logged_out": True})
        response.delete_cookie("ai_manager_session", path="/")
        return response

    @app.post("/api/sessions/revoke", dependencies=[Depends(require_owner)])
    def revoke_sessions():
        with db.transaction() as conn:
            conn.execute("DELETE FROM sessions")
            db.event(conn, "owner.sessions_revoked", {})
        return {"revoked": True}

    @app.get("/api/portfolio", dependencies=[Depends(require_owner)])
    def portfolio():
        projects = registry.list()
        storage = shutil.disk_usage(settings.data_dir)
        return {
            "projects": projects,
            "tasks": [{**task, "readiness": workers.readiness(task["id"])} for task in tasks.list()],
            "executions": db.all("SELECT * FROM executions ORDER BY started_at DESC LIMIT 50"),
            "checkpoints": db.all("SELECT * FROM checkpoints ORDER BY created_at DESC LIMIT 20"),
            "recommendation": "Focus on fewer active projects"
            if sum(p["status"] == "active" for p in projects) > 3
            else "Continue the highest priority unblocked task",
            "automation_enabled": db.setting("scheduler_enabled", False),
            "scheduler": scheduler.snapshot(),
            "approvals": db.all("SELECT * FROM approvals WHERE status='pending' ORDER BY created_at"),
            "notifications": db.all("SELECT * FROM notifications ORDER BY created_at DESC LIMIT 30"),
            "storage": {
                "free_bytes": storage.free,
                "total_bytes": storage.total,
                "low_space": storage.free < 1024 * 1024 * 1024,
            },
        }

    @app.post("/api/projects/discover", dependencies=[Depends(require_owner)])
    def discover():
        return registry.discover()

    @app.post("/api/projects", dependencies=[Depends(require_owner)])
    def add_project(body: ProjectBody):
        return registry.add(body.path, body.name)

    @app.patch("/api/projects/{project_id}", dependencies=[Depends(require_owner)])
    def update_project(project_id: str, body: ProjectUpdate):
        return registry.update(project_id, body.revision, body.model_dump(exclude={"revision"}, exclude_none=True))

    @app.get("/api/projects/{project_id}", dependencies=[Depends(require_owner)])
    def detail(project_id: str):
        return {
            "project": registry.get(project_id),
            "memory": registry.memory(project_id),
            "tasks": tasks.list(project_id),
            "checkpoint": db.one(
                "SELECT * FROM checkpoints WHERE project_id=? ORDER BY created_at DESC LIMIT 1", (project_id,)
            ),
            "observation": registry.reconcile(project_id),
        }

    @app.put("/api/projects/{project_id}/memory/{name}", dependencies=[Depends(require_owner)])
    def update_memory(project_id: str, name: str, body: MemoryBody):
        registry.update_memory(project_id, name, body.content, body.revision)
        return {"updated": True}

    @app.post("/api/tasks", dependencies=[Depends(require_owner)])
    def add_task(body: TaskBody):
        return tasks.add(**body.model_dump())

    @app.post("/api/tasks/{task_id}/start", dependencies=[Depends(require_owner)])
    async def start_task(task_id: str):
        return await workers.start(task_id)

    @app.post("/api/goals", dependencies=[Depends(require_owner)])
    async def goal(body: GoalBody):
        result = await planner.plan(body.project_id, body.goal, body.request_key)
        await scheduler.tick()
        return {
            **result,
            "result": "accepted",
            "tasks": scheduler.receipts(result.get("task_ids", [])),
            "scheduler": scheduler.snapshot(),
        }

    @app.put("/api/scheduler", dependencies=[Depends(require_owner)])
    def configure_scheduler(body: SchedulerBody):
        if body.project_id:
            registry.get(body.project_id)
        db.set_setting("scheduler_scope", body.project_id)
        db.set_setting("scheduler_batch", {"limit": body.limit, "task_ids": []})
        db.set_setting("scheduler_enabled", body.enabled)
        db.set_setting("max_workers", body.max_workers)
        settings.max_workers = body.max_workers
        return {
            "configured": True,
            "result": "accepted",
            "message": "Scheduler enabled" if body.enabled else "Scheduler paused",
            "scheduler": scheduler.snapshot(),
        }

    @app.get("/api/projects/{project_id}/verification", dependencies=[Depends(require_owner)])
    def get_verification(project_id: str):
        registry.get(project_id)
        return {"commands": db.setting(f"verification_commands:{project_id}", [])}

    @app.put("/api/projects/{project_id}/verification", dependencies=[Depends(require_owner)])
    def verification_commands(project_id: str, body: VerificationCommands):
        registry.get(project_id)
        if any(
            not command or len(command) > 30 or any(len(arg) > 1000 for arg in command) for command in body.commands
        ):
            raise ValueError("Invalid verification command")
        with db.transaction() as conn:
            import json

            conn.execute(
                "INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (f"verification_commands:{project_id}", json.dumps(body.commands)),
            )
            db.event(conn, "verification.configured", {"commands": body.commands, "configured_by": "owner"}, project_id)
        return {
            "updated": True,
            "result": "accepted",
            "message": "Trusted verification commands saved. Retry verification on blocked tasks; execution is not replayed automatically.",
            "sandbox": "No host home or network; system runtime and workspace only",
        }

    @app.post("/api/tasks/{task_id}/resume", dependencies=[Depends(require_owner)])
    async def resume_task(task_id: str, body: ResumeBody):
        return await workers.resume(task_id, body.revision, body.reconciled, body.reconciliation)

    @app.post("/api/tasks/{task_id}/retry", dependencies=[Depends(require_owner)])
    async def retry_task(task_id: str, body: ResumeBody):
        return await workers.retry(task_id, body.revision)

    @app.post("/api/tasks/{task_id}/verify", dependencies=[Depends(require_owner)])
    async def retry_verification(task_id: str, body: ResumeBody):
        result = verifier.retry(task_id, body.revision)
        await scheduler.tick()
        return result

    @app.patch("/api/tasks/{task_id}", dependencies=[Depends(require_owner)])
    def task_control(task_id: str, body: TaskControl):
        task = tasks.get(task_id)
        if task["status"] in {"running", "verifying", "awaiting_approval"} or body.status not in {
            "pending",
            "paused",
            "cancelled",
        }:
            raise ValueError("Use worker interruption controls for active work; completion requires verification")
        if body.status == "pending" and workers.latest_worker(task_id):
            raise ValueError(
                "Use Retry launch, Resume persisted worker, or Retry verification; existing executions must not be replayed by a generic state change"
            )
        result = tasks.transition(task_id, body.status, body.revision)
        return {**result, "result": "accepted", "message": f"Task {task_id[:8]} is {result['status']}"}

    @app.get("/api/history", dependencies=[Depends(require_owner)])
    def history(project_id: str | None = None, search: str = ""):
        return chat.history(project_id, search)

    @app.get("/api/tasks/{task_id}/evidence", dependencies=[Depends(require_owner)])
    def evidence(task_id: str):
        tasks.get(task_id)
        return db.all("SELECT * FROM verification_results WHERE task_id=? ORDER BY created_at DESC", (task_id,))

    @app.get("/api/tasks/{task_id}/execution-records", dependencies=[Depends(require_owner)])
    def execution_records(task_id: str):
        return {
            "task": tasks.get(task_id),
            "readiness": workers.readiness(task_id),
            "executions": db.all("SELECT * FROM executions WHERE task_id=? ORDER BY started_at", (task_id,)),
            "operations": db.all(
                "SELECT o.* FROM operations o JOIN executions e ON e.id=o.execution_id WHERE e.task_id=? ORDER BY o.created_at",
                (task_id,),
            ),
            "approvals": db.all("SELECT * FROM approvals WHERE task_id=? ORDER BY created_at", (task_id,)),
            "verification": db.all(
                "SELECT * FROM verification_results WHERE task_id=? ORDER BY created_at", (task_id,)
            ),
        }

    @app.get("/api/executions/{execution_id}/patch", dependencies=[Depends(require_owner)])
    def download_patch(execution_id: str):
        execution = db.one("SELECT * FROM executions WHERE id=? AND task_id IS NOT NULL", (execution_id,))
        if not execution or not execution["worktree"] or tasks.get(execution["task_id"])["status"] != "completed":
            raise HTTPException(409, "A patch is available only after verified completion")
        root = Path(execution["worktree"])
        if not root.resolve().is_relative_to((settings.data_dir / "workspaces").resolve()):
            raise ValueError("Result is outside isolated storage")
        baseline = execution.get("base_commit") or execution["commit_ref"]
        changed = git(root, "diff", baseline, "--name-only", "-z").split("\0")
        untracked = git(root, "ls-files", "--others", "--exclude-standard", "-z").split("\0")
        for path in changed + untracked:
            if path:
                safe_file(root, path)
        patch = git(root, "diff", "--binary", baseline, "--")
        import subprocess

        for path in untracked:
            if path:
                result = subprocess.run(
                    [
                        "git",
                        "-C",
                        str(root),
                        "-c",
                        "core.hooksPath=/dev/null",
                        "diff",
                        "--no-index",
                        "--binary",
                        "--",
                        "/dev/null",
                        path,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    env={
                        "PATH": "/usr/bin:/bin",
                        "GIT_CONFIG_GLOBAL": "/dev/null",
                        "GIT_CONFIG_SYSTEM": "/dev/null",
                        "GIT_CONFIG_NOSYSTEM": "1",
                    },
                )
                patch += "\n" + result.stdout
        return PlainTextResponse(
            patch + "\n",
            headers={"Content-Disposition": f'attachment; filename="ai-manager-{execution_id[:12]}.patch"'},
        )

    @app.post("/api/chat", dependencies=[Depends(require_owner)])
    async def submit(body: MessageBody):
        return await chat.submit(body.project_id, body.content, body.mode)

    @app.post("/api/executions/{execution_id}/interrupt", dependencies=[Depends(require_owner)])
    async def interrupt(execution_id: str):
        execution = db.one("SELECT task_id FROM executions WHERE id=?", (execution_id,))
        if execution and execution["task_id"]:
            await workers.interrupt(execution_id)
        else:
            await chat.interrupt(execution_id)
        return {
            "requested": True,
            "result": "accepted",
            "status": "interruption_requested",
            "message": "Interruption requested; waiting for confirmed turn outcome",
        }

    @app.get("/api/executions/{execution_id}/events", dependencies=[Depends(require_owner)])
    async def execution_events(execution_id: str, request: Request, after: int = 0):
        if not db.one("SELECT id FROM executions WHERE id=?", (execution_id,)):
            raise HTTPException(404, "Unknown execution")
        header = request.headers.get("last-event-id", "0")
        cursor = max(after, int(header) if header.isdecimal() else 0)

        async def stream():
            nonlocal cursor
            while not await request.is_disconnected():
                if not auth.session(request.cookies.get("ai_manager_session")):
                    yield "event: stream.auth_expired\ndata: {}\n\n"
                    return
                rows = db.all(
                    "SELECT * FROM events WHERE execution_id=? AND seq>? ORDER BY seq LIMIT 100", (execution_id, cursor)
                )
                for row in rows:
                    cursor = row["seq"]
                    yield f"id: {cursor}\nevent: {row['kind']}\ndata: {row['payload']}\n\n"
                execution = db.one("SELECT finished_at FROM executions WHERE id=?", (execution_id,))
                if execution["finished_at"] and len(rows) < 100:
                    yield "event: stream.end\ndata: {}\n\n"
                    return
                yield ": heartbeat\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

    @app.get("/api/models", dependencies=[Depends(require_owner)])
    async def models():
        routes = await chat.routes()
        return {
            "models": chat.server.catalog,
            "routes": routes,
            "account_type": chat.server.account_type,
            "catalog_is_entitlement_check": False,
            "verified_usage": db.all(
                "SELECT model,effort,status,latency_ms,usage FROM executions WHERE finished_at IS NOT NULL ORDER BY started_at DESC LIMIT 30"
            ),
        }

    @app.put("/api/models/{role}", dependencies=[Depends(require_owner)])
    async def model_route(role: str, body: RouteBody):
        if role not in ROLES:
            raise ValueError("Unknown model role")
        await chat.server.start()
        route = chat.server.route(body.model, body.effort)
        routes = db.setting("model_routes", {})
        routes[role] = route
        db.set_setting("model_routes", routes)
        return route

    @app.get("/api/settings", dependencies=[Depends(require_owner)])
    def get_settings():
        return {
            "global_instructions": db.setting("global_instructions", ""),
            "max_workers": settings.max_workers,
            "project_roots": [str(p) for p in settings.project_roots],
            "scheduler_enabled": db.setting("scheduler_enabled", False),
            "scheduler_scope": db.setting("scheduler_scope", None),
            "scheduler_batch": db.setting("scheduler_batch", {}),
            "daily_summaries": db.setting("daily_summaries", False),
        }

    @app.put("/api/settings/instructions", dependencies=[Depends(require_owner)])
    def global_instructions(body: InstructionsBody):
        db.set_setting("global_instructions", body.content)
        return {"updated": True}

    @app.get("/api/policies", dependencies=[Depends(require_owner)])
    def get_policies(project_id: str | None = None):
        if project_id:
            registry.get(project_id)
        return policies.list(project_id)

    @app.put("/api/policies/{action}", dependencies=[Depends(require_owner)])
    def set_policy(action: str, body: PolicyBody, project_id: str | None = None):
        if project_id:
            registry.get(project_id)
        return policies.set(project_id, action, body.mode, body.risk_accepted)

    @app.get("/api/approvals", dependencies=[Depends(require_owner)])
    def get_approvals():
        return db.all("SELECT * FROM approvals ORDER BY created_at DESC LIMIT 100")

    @app.post("/api/approvals/{approval_id}/decision", dependencies=[Depends(require_owner)])
    def decide(approval_id: str, body: ApprovalBody):
        policies.decide(approval_id, body.approve)
        return {"decided": True}

    @app.put("/api/settings/notifications", dependencies=[Depends(require_owner)])
    def notification_settings(body: NotificationSettings):
        db.set_setting("daily_summaries", body.daily_summaries)
        return {"updated": True}

    @app.post("/api/notifications/{notification_id}/read", dependencies=[Depends(require_owner)])
    def read_notification(notification_id: str):
        import time

        with db.transaction() as conn:
            conn.execute("UPDATE notifications SET read_at=? WHERE id=?", (time.time(), notification_id))
        return {"read": True}

    @app.get("/api/audit", dependencies=[Depends(require_owner)])
    def audit(after: int = 0):
        return db.all("SELECT * FROM events WHERE seq>? ORDER BY seq DESC LIMIT 100", (after,))

    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/")
    def index():
        return FileResponse(static / "index.html")

    return app
