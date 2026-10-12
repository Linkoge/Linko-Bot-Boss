import asyncio
import fcntl
import hmac
import json
import logging
import shutil
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .auth import Auth
from .chat import ROLES, Chat
from .codex import AppServer, CodexError
from .config import Settings
from .db import Database
from .execution_ui import execution_summary
from .orchestration import Scheduler
from .registry import Registry, git
from .tasks import Tasks
from .todos import Todos
from .workers import Workers


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


class MessageBody(BaseModel):
    request_id: str | None = Field(default=None, min_length=16, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    project_id: str | None = None
    content: str = Field(min_length=1, max_length=24000)
    conversation_id: str | None = None
    mode: Literal["conversation", "auto", "execute"] = "auto"


class RouteBody(BaseModel):
    model: str
    effort: str


class ChatResetBody(BaseModel):
    project_id: str | None = None
    conversation_id: str = Field(min_length=1, max_length=100)
    last_message_id: str = Field(min_length=1, max_length=100)
    confirmed: bool = False
    title: str = Field(default="", max_length=120)


class InstructionsBody(BaseModel):
    content: str = Field(max_length=10000)


class TodoBody(BaseModel):
    project_id: str
    title: str = Field(min_length=1, max_length=200)
    details: str = Field(default="", max_length=24000)
    revision: int | None = None


class TodoSelection(BaseModel):
    id: str
    revision: int


class TodoArchive(BaseModel):
    project_id: str | None = None
    selected: list[TodoSelection] = Field(min_length=1, max_length=200)


class DeploymentCancel(BaseModel):
    nonce: str = Field(pattern=r"^[0-9a-f]{32}$")


class TaskControl(BaseModel):
    revision: int
    action: str


class AnswerBody(BaseModel):
    answers: dict


def create_app(settings: Settings | None = None, server=None, worker_factory=AppServer):
    settings = settings or Settings()
    settings.prepare()
    db = Database(settings.db_path)
    registry, tasks, auth = Registry(db, settings.project_roots), Tasks(db), Auth(db, settings)
    chat = Chat(db, registry, server or AppServer(settings))
    workers = Workers(db, registry, settings, worker_factory)
    scheduler = Scheduler(db, registry, settings, workers, chat)
    chat.action_handler = scheduler.manager_action
    release_commit = git(Path(__file__).resolve().parents[1], "rev-parse", "HEAD")

    @asynccontextmanager
    async def lifespan(app):
        lock_file = (settings.data_dir / "manager.lock").open("a")
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            auth.bootstrap()
            registry.discover()
            settings.max_workers = db.setting("max_workers", settings.max_workers)
            scheduler.start()
            yield
        finally:
            await scheduler.close()
            await workers.close()
            await chat.close()
            lock_file.close()

    app = FastAPI(title="Linko Manager", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.db, app.state.registry, app.state.chat, app.state.auth = db, registry, chat, auth
    app.state.workers, app.state.scheduler = workers, scheduler
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts))

    app.state.deployment_mutations = 0
    scheduler.deployment_busy = lambda: app.state.deployment_mutations > 0

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
        mutation = request.method not in {'GET', 'HEAD', 'OPTIONS'}
        drain = request.url.path in {'/api/login', '/api/deployment/cancel'} or request.url.path.endswith('/answer') or request.url.path.endswith('/interrupt')
        if request.method == 'PATCH' and request.url.path.startswith('/api/tasks/'):
            body = await request.json()
            drain = body.get('action') in {'stop', 'pause', 'cancel'}
        if mutation and scheduler.deployment.gated() and not drain:
            return JSONResponse({'detail': 'Deployment pending; wait for activation'}, status_code=503)
        if mutation and scheduler.deployment.ready_to_stop():
            return JSONResponse({'detail': 'Deployment handoff acknowledged'}, status_code=503)
        if mutation:
            app.state.deployment_mutations += 1
        try:
            response = await call_next(request)
        finally:
            if mutation:
                app.state.deployment_mutations -= 1
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
            "release_commit": release_commit,
            "scheduler_running": bool(scheduler.job and not scheduler.job.done()),
        }

    @app.get('/api/deployment', dependencies=[Depends(require_owner)])
    def deployment_status():
        return scheduler.deployment.status()

    @app.post('/api/deployment/cancel', dependencies=[Depends(require_owner)])
    def cancel_deployment(body: DeploymentCancel):
        return scheduler.deployment.cancel(body.nonce)

    @app.post("/api/setup")
    async def setup(body: SetupBody, request: Request):
        # Bootstrap token checks are also throttled, with no remote password reset path.

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

    @app.get("/api/history", dependencies=[Depends(require_owner)])
    def history(project_id: str | None = None, search: str = ""):
        return chat.history(project_id, search)

    @app.get("/api/chat-history", dependencies=[Depends(require_owner)])
    def search_history(
        project_id: str | None = None,
        search: str = Query(default="", max_length=500),
        offset: int = Query(default=0, ge=0),
    ):
        return chat.search_history(project_id, search, offset)

    @app.get("/api/chat-history/{conversation_id}", dependencies=[Depends(require_owner)])
    def saved_history(
        conversation_id: str,
        project_id: str | None = None,
        before: str | None = None,
        search: str = Query(default="", max_length=500),
    ):
        return chat.saved_history(conversation_id, project_id, before, search)

    @app.post("/api/chat/clear", dependencies=[Depends(require_owner)])
    def clear_chat(body: ChatResetBody):
        return chat.reset(body.project_id, body.conversation_id, body.last_message_id, body.confirmed)

    @app.post("/api/chat/archive", dependencies=[Depends(require_owner)])
    def archive_chat(body: ChatResetBody):
        return chat.reset(
            body.project_id, body.conversation_id, body.last_message_id, body.confirmed, archive=True, title=body.title
        )

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

    @app.put("/api/settings/instructions", dependencies=[Depends(require_owner)])
    def global_instructions(body: InstructionsBody):
        db.set_setting("global_instructions", body.content)
        return {"updated": True}

    @app.get("/")
    def index():
        return FileResponse(static / "index.html")

    @app.get("/api/todos", dependencies=[Depends(require_owner)])
    def todo_list(project_id: str | None = None):
        if project_id:
            registry.get(project_id)
        return {"entries": Todos(db).list(project_id)}

    @app.post("/api/todos", dependencies=[Depends(require_owner)])
    def todo_add(body: TodoBody):
        return Todos(db).save(body.project_id, body.title, body.details)

    @app.patch("/api/todos/{entry_id}", dependencies=[Depends(require_owner)])
    def todo_update(entry_id: str, body: TodoBody):
        return Todos(db).save(body.project_id, body.title, body.details, entry_id, body.revision)

    @app.post("/api/todos/archive", dependencies=[Depends(require_owner)])
    def todo_archive(body: TodoArchive):
        return Todos(db).archive([e.model_dump() for e in body.selected], body.project_id)

    @app.get("/api/portfolio", dependencies=[Depends(require_owner)])
    def portfolio(project_id: str | None = None):
        if project_id:
            registry.get(project_id)
        return {
            "scope_project_id": project_id,
            "projects": registry.list(),
            "global_last_used_at": db.setting("global_last_used_at", 0),
            "tasks": tasks.list(project_id),
            **execution_summary(db, project_id),
            "questions": db.all(
                "SELECT q.*,e.project_id,e.task_id FROM questions q JOIN executions e ON e.id=q.execution_id WHERE q.status='pending'"
                + (" AND e.project_id=?" if project_id else ""),
                (project_id,) if project_id else (),
            ),
            "capacity": settings.max_workers,
            "queue_error": scheduler.error,
            "storage_free": shutil.disk_usage(settings.data_dir).free,
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
        }

    @app.post("/api/chat", dependencies=[Depends(require_owner)])
    async def submit(body: MessageBody):
        return await chat.submit(
            body.project_id, body.content, body.conversation_id, mode=body.mode, request_id=body.request_id
        )

    @app.patch("/api/tasks/{task_id}", dependencies=[Depends(require_owner)])
    def task_control(task_id: str, body: TaskControl):
        return tasks.control(task_id, body.action, body.revision)

    @app.get("/api/tasks/{task_id}/execution-records", dependencies=[Depends(require_owner)])
    def records(task_id: str):
        historical = {
            "historical_" + label: db.all(f"SELECT * FROM {table} WHERE task_id=?", (task_id,))
            if db.one("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,))
            else []
            for label, table in (("verification", "verification_results"), ("integration", "integration_results"))
        }
        return {
            "task": tasks.get(task_id),
            "evidence": db.all(
                "SELECT payload,created_at FROM events WHERE task_id=? AND kind='task.evidence' ORDER BY seq DESC LIMIT 5",
                (task_id,),
            ),
            "executions": db.all("SELECT * FROM executions WHERE task_id=? ORDER BY started_at", (task_id,)),
            **historical,
        }

    @app.post("/api/executions/{execution_id}/interrupt", dependencies=[Depends(require_owner)])
    async def interrupt(execution_id: str):
        execution = db.one("SELECT task_id FROM executions WHERE id=?", (execution_id,))
        if execution and execution["task_id"]:
            await workers.interrupt(execution_id)
        else:
            await chat.interrupt(execution_id)
        return {"result": "accepted"}

    @app.post("/api/questions/{question_id}/answer", dependencies=[Depends(require_owner)])
    def answer(question_id: str, body: AnswerBody):
        with db.transaction() as conn:
            question = conn.execute(
                "SELECT q.* FROM questions q JOIN executions e ON e.id=q.execution_id WHERE q.id=? AND q.status='pending' AND e.finished_at IS NULL",
                (question_id,),
            ).fetchone()
            if not question:
                raise ValueError("Question is no longer pending")
            if question["kind"] == "item/tool/requestUserInput":
                required = {x["id"] for x in json.loads(question["request"])["questions"]}
                if set(body.answers) != required or not all(
                    isinstance(x, str) and x.strip() and len(x) <= 10000 for x in body.answers.values()
                ):
                    raise ValueError("Provide an answer to each question")
            elif set(body.answers) != {"approve"} or not isinstance(body.answers["approve"], bool):
                raise ValueError("Choose approve or decline for the exact action")
            conn.execute(
                "UPDATE questions SET answer=?,status='answered' WHERE id=?", (json.dumps(body.answers), question_id)
            )
            db.event(conn, "question.answered", {"question_id": question_id}, execution_id=question["execution_id"])
        return {"result": "accepted"}

    @app.get("/api/settings", dependencies=[Depends(require_owner)])
    def get_settings():
        return {"global_instructions": db.setting("global_instructions", ""), "max_workers": settings.max_workers}

    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")
    return app
