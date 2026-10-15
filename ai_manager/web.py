import asyncio
import fcntl
import hashlib
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
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .account_usage import AccountUsage
from .attachments import Attachments
from .audio import Audio
from .auth import Auth
from .chat import Chat
from .codex import CodexError
from .config import Settings
from .core import Core, Tasks
from .db import Database
from .execution_ui import event_page, execution_page, execution_summary
from .orchestration import Scheduler
from .pipeline import Pipeline
from .project_memory import MemoryReviews, ProjectMemory
from .registry import Registry, git
from .todos import Todos


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=1024)


class SetupBody(LoginBody):
    token: str = Field(min_length=20, max_length=100)


class ProjectBody(BaseModel):
    path: str = Field(max_length=4096)
    name: str | None = Field(default=None, max_length=200)


class ProjectVisibility(BaseModel):
    visible: bool = Field(strict=True)


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
    content: str = Field(default='', max_length=64000)
    attachment_ids: list[str] = Field(default_factory=list, max_length=4)
    todos: list[str] = Field(default_factory=list, max_length=200)
    conversation_id: str | None = None


class ChatResetBody(BaseModel):
    project_id: str | None = None
    conversation_id: str = Field(min_length=1, max_length=100)
    last_message_id: str = Field(min_length=1, max_length=100)
    confirmed: bool = False
    title: str = Field(default="", max_length=120)


class ChatMessageDeleteBody(BaseModel):
    project_id: str | None = None
    conversation_id: str = Field(min_length=1, max_length=100)
    message_id: str = Field(min_length=1, max_length=100)
    confirmed: bool = Field(default=False, strict=True)


class ChatTrashBody(BaseModel):
    project_id: str | None = None
    conversation_id: str = Field(min_length=1, max_length=100)
    last_message_id: str | None = Field(default=None, max_length=100)
    trash_id: str = Field(min_length=1, max_length=100)
    confirmed: bool = False


class InstructionsBody(BaseModel):
    content: str = Field(max_length=10000)


class TodoBody(BaseModel):
    project_id: str
    title: str = Field(min_length=1, max_length=200)
    details: str = Field(default="", max_length=24000)


class TodoArchive(BaseModel):
    project_id: str | None = None
    selected: list[str] = Field(min_length=1, max_length=200)


class MemoryStart(BaseModel):
    confirmed: bool = False


class MemoryApply(BaseModel):
    review: str
    revision: int
    resolutions: list[dict] = Field(default_factory=list)


class MemoryRollback(BaseModel):
    version: str
    revision: int


class MemoryCancel(BaseModel):
    review: str


class TaskControl(BaseModel):
    revision: int
    action: Literal["hold", "release", "continue", "cancel"]


class AnswerBody(BaseModel):
    answers: dict


def create_app(settings: Settings | None = None, server=None, worker_factory=None):
    settings = settings or Settings()
    settings.prepare()
    db = Database(settings.db_path)
    registry, tasks, auth = Registry(db, settings.project_roots), Tasks(db), Auth(db, settings)
    from .model_settings import active
    from .providers import ProviderServer

    def provider_factory(settings, native=False):
        return ProviderServer(settings, native, active=lambda: active(db))

    worker_factory = worker_factory or provider_factory
    chat = Chat(db, registry, server or provider_factory(settings))
    account_usage = AccountUsage(chat.server)
    audio = Audio(settings)
    attachments = Attachments(db, settings)
    chat.attachments = attachments
    pipeline = Pipeline(db, registry)
    core = Core(db, registry, settings, pipeline, worker_factory)
    scheduler = Scheduler(db, registry, settings, core, chat, pipeline)
    chat.action_handler = scheduler.manager_action
    memory = ProjectMemory(db, registry)
    reviews = MemoryReviews(memory, settings)
    release_commit = git(Path(__file__).resolve().parents[1], "rev-parse", "HEAD")

    @asynccontextmanager
    async def lifespan(app):
        lock_file = (settings.data_dir / "manager.lock").open("a")
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            auth.bootstrap()
            registry.discover()
            reviews.recover()
            scheduler.start()
            yield
        finally:
            await reviews.close()
            await scheduler.close()
            await core.close()
            await chat.close()
            lock_file.close()

    app = FastAPI(title="Linko Bot Boss", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.db, app.state.registry, app.state.chat, app.state.auth = db, registry, chat, auth
    app.state.core, app.state.scheduler = core, scheduler
    app.state.audio = audio
    app.state.attachments = attachments
    app.state.memory, app.state.memory_reviews = memory, reviews
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
        response.headers.setdefault("Cache-Control", "no-store")
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

    @app.get('/api/repair', dependencies=[Depends(require_owner)])
    def repair_status():
        from scripts.emergency_repair import Repair
        return Repair(settings.data_dir).records()

    @app.post('/api/repair', dependencies=[Depends(require_owner)])
    def request_repair(body: dict):
        from scripts.emergency_repair import Repair
        conversation = body.get('conversation_id')
        if not db.one('SELECT 1 FROM conversations WHERE id=?', (conversation,)):
            raise HTTPException(400, 'Select an existing originating conversation')
        return Repair(settings.data_dir).submit(body.get('request_id'), body.get('instructions', ''), conversation,
                                                body.get('provider'), body.get('model'))

    @app.get('/api/deployment', dependencies=[Depends(require_owner)])
    def deployment_status():
        return scheduler.pipeline.status()

    @app.post('/api/deployment/cancel', dependencies=[Depends(require_owner)])
    def cancel_deployment():
        return {'cancelled': scheduler.pipeline.cancel()}

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
    def history(project_id: str | None = None, search: str = "", before: str | None = None):
        return chat.history(project_id, search, before)

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

    @app.post("/api/chat/message/delete", dependencies=[Depends(require_owner)])
    def delete_chat_message(body: ChatMessageDeleteBody):
        return chat.delete_message(**body.model_dump())

    @app.post("/api/chat/clear", dependencies=[Depends(require_owner)])
    def clear_chat(body: ChatResetBody):
        return chat.reset(body.project_id, body.conversation_id, body.last_message_id, body.confirmed)

    @app.post("/api/chat/trash/restore", dependencies=[Depends(require_owner)])
    def restore_trash(body: ChatTrashBody):
        return chat.change_trash(**body.model_dump(), restore=True)

    @app.post("/api/chat/trash/empty", dependencies=[Depends(require_owner)])
    def empty_trash(body: ChatTrashBody):
        return chat.change_trash(**body.model_dump())

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

    @app.get("/api/account-usage", dependencies=[Depends(require_owner)])
    async def usage():
        return await account_usage.read()

    @app.get("/api/audio", dependencies=[Depends(require_owner)])
    def audio_status():
        return audio.status()

    @app.post('/api/attachments', dependencies=[Depends(require_owner)])
    async def upload_attachment(request: Request, name: str, conversation_id: str, project_id: str | None = None):
        return await attachments.upload(request, name, project_id, conversation_id)

    @app.get('/api/attachments/{identifier}', dependencies=[Depends(require_owner)])
    def download_attachment(identifier: str):
        item = attachments.get(identifier)
        return FileResponse(attachments.root / item['stored_name'], media_type='application/octet-stream',
                            filename=item['name'], headers={'X-Content-Type-Options': 'nosniff'})

    @app.post("/api/audio/transcribe", dependencies=[Depends(require_owner)])
    async def transcribe_audio(request: Request):
        return await audio.transcribe(request)

    @app.get("/api/model-settings", dependencies=[Depends(require_owner)])
    async def model_settings(project_id: str | None = None):
        from .model_settings import active, choices, current, favorites, project_worker
        try:
            await chat.server.start()
        except Exception:
            pass  # Show the saved choices; unavailable models render disabled.
        project_choice = project_worker(db, project_id) if project_id else None
        saved, marked = current(db, chat.server), favorites(db, chat.server)
        effective = current(db, chat.server, project_id)
        keep = {pair.get("model") for pair in (saved["manager"], effective['worker'],
                                               *(f["pair"] for f in marked if f)) if pair}
        return {"provider": active(db), "settings": saved, "favorites": marked,
                "project_id": project_id, "project_worker": project_choice,
                "worker_selection": {k: effective[k] for k in ('worker', 'manual')},
                "providers": {p: {"choices": choices(chat.server, p, keep)} for p in ("codex", "claude")}}

    @app.put("/api/projects/{project_id}/worker-model", dependencies=[Depends(require_owner)])
    async def save_worker_override(project_id: str, body: dict):
        from .model_settings import save_project_worker
        await chat.server.start()
        save_project_worker(db, chat.server, project_id, body)
        return await model_settings(project_id)

    @app.put("/api/model-settings", dependencies=[Depends(require_owner)])
    async def save_model_settings(body: dict):
        from .model_settings import save
        await chat.server.start()
        result = save(db, chat.server, body)
        account_usage.invalidate()
        return result

    @app.put("/api/manager-favorites/{slot}", dependencies=[Depends(require_owner)])
    async def insert_manager_favorite(slot: int):
        from .model_settings import insert_favorite
        await chat.server.start()
        return insert_favorite(db, chat.server, slot)

    @app.post("/api/manager-favorites/{slot}/recall", dependencies=[Depends(require_owner)])
    async def recall_manager_favorite(slot: int, project_id: str | None = None):
        from .model_settings import recall_favorite
        await chat.server.start()
        recall_favorite(db, chat.server, slot)
        account_usage.invalidate()
        return await model_settings(project_id)

    @app.put("/api/settings/instructions", dependencies=[Depends(require_owner)])
    def global_instructions(body: InstructionsBody):
        db.set_setting("global_instructions", body.content)
        return {"updated": True}

    @app.get("/")
    def index():
        return HTMLResponse(index_html)

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
        return Todos(db).save(body.project_id, body.title, body.details, entry_id)

    @app.post("/api/todos/archive", dependencies=[Depends(require_owner)])
    def todo_archive(body: TodoArchive):
        return Todos(db).archive(body.selected, body.project_id)

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
            "capacity": 1,
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

    @app.put("/api/projects/{project_id}/visibility", dependencies=[Depends(require_owner)])
    def project_visibility(project_id: str, body: ProjectVisibility):
        return registry.set_visibility(project_id, body.visible)

    @app.get("/api/projects/{project_id}", dependencies=[Depends(require_owner)])
    def detail(project_id: str):
        return {
            "project": registry.get(project_id),
            "memory": registry.memory(project_id),
            "tasks": tasks.list(project_id),
        }

    @app.get("/api/projects/{project_id}/brief", dependencies=[Depends(require_owner)])
    def memory_brief(project_id: str):
        snapshot = memory.snapshot(project_id)
        return {**snapshot, "review_records": [
            {"reference": ref, **memory.get(project_id, ref)} for ref in reversed(snapshot["reviews"])
        ]}

    @app.get("/api/projects/{project_id}/brief/version", dependencies=[Depends(require_owner)])
    def memory_version(project_id: str, reference: str):
        snapshot = memory.snapshot(project_id)
        if reference not in snapshot["versions"]:
            raise ValueError("Unknown version in this project")
        return memory.get(project_id, reference)

    @app.post("/api/projects/{project_id}/brief/review", dependencies=[Depends(require_owner)])
    async def memory_review(project_id: str, body: MemoryStart):
        return reviews.start(project_id, body.confirmed)

    @app.post("/api/projects/{project_id}/brief/cancel", dependencies=[Depends(require_owner)])
    async def memory_cancel(project_id: str, body: MemoryCancel):
        return await reviews.cancel(project_id, body.review)

    @app.post("/api/projects/{project_id}/brief/apply", dependencies=[Depends(require_owner)])
    def memory_apply(project_id: str, body: MemoryApply):
        return memory.apply(project_id, body.review, body.revision, body.resolutions)

    @app.post("/api/projects/{project_id}/brief/rollback", dependencies=[Depends(require_owner)])
    def memory_rollback(project_id: str, body: MemoryRollback):
        return memory.rollback(project_id, body.version, body.revision)

    @app.post("/api/chat", dependencies=[Depends(require_owner)])
    async def submit(body: MessageBody):
        return await chat.submit(
            body.project_id, body.content, body.conversation_id, request_id=body.request_id,
            attachment_ids=body.attachment_ids, todos=body.todos
        )

    @app.patch("/api/tasks/{task_id}", dependencies=[Depends(require_owner)])
    async def task_control(task_id: str, body: TaskControl):
        return await core.control(task_id, body.action, body.revision)

    @app.get("/api/tasks/{task_id}/execution-records", dependencies=[Depends(require_owner)])
    def records(task_id: str, offset: int = 0, before_event: int | None = None, before_execution: str | None = None):
        # Tables and events from earlier eras stay readable; nothing writes them any more.
        historical = {
            "historical_" + label: db.all(f"SELECT * FROM {table} WHERE task_id=?", (task_id,))
            if db.one("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,))
            else []
            for label, table in (("verification", "verification_results"), ("integration", "integration_results"))
        }
        return {
            "task": tasks.get(task_id),
            "checks": db.all(
                "SELECT payload,created_at FROM events WHERE task_id=? AND kind IN ('task.check','task.round') ORDER BY seq DESC LIMIT 20",
                (task_id,),
            ),
            "evidence": db.all(
                "SELECT payload,created_at FROM events WHERE task_id=? AND kind='task.evidence' ORDER BY seq DESC LIMIT 5",
                (task_id,),
            ),
            **execution_page(db, task_id=task_id, offset=offset, before=before_execution),
            **event_page(db, task_id=task_id, before=before_event),
            **historical,
        }

    @app.get('/api/execution-history', dependencies=[Depends(require_owner)])
    def execution_history(project_id: str | None = None, offset: int = 0, before_execution: str | None = None):
        return execution_page(db, project_id=project_id, offset=offset, before=before_execution)

    @app.get('/api/executions/{execution_id}/history', dependencies=[Depends(require_owner)])
    def worker_history(execution_id: str, before_event: int | None = None, output_offset: int | None = None):
        row = db.one('SELECT id,history_preview(output) AS result_summary FROM executions WHERE id=?', (execution_id,))
        if not row:
            raise ValueError('Unknown execution')
        result = {**row, **event_page(db, execution_id=execution_id, before=before_event)}
        if output_offset is not None:
            if output_offset < 0:
                raise ValueError('Offset must be nonnegative')
            full = db.one('SELECT output FROM executions WHERE id=?', (execution_id,))['output']
            result.update(output=full[output_offset:output_offset + 12000],
                          next_output_offset=output_offset + 12000 if len(full) > output_offset + 12000 else None)
        return result

    @app.post("/api/executions/{execution_id}/interrupt", dependencies=[Depends(require_owner)])
    async def interrupt(execution_id: str):
        execution = db.one("SELECT task_id FROM executions WHERE id=?", (execution_id,))
        if execution and execution["task_id"]:
            await core.cancel(execution_id)
        else:
            await chat.interrupt(execution_id)
        return {"result": "accepted"}

    @app.post("/api/questions/{question_id}/answer", dependencies=[Depends(require_owner)])
    def answer(question_id: str, body: AnswerBody):
        with db.transaction() as conn:
            question = conn.execute(
                "SELECT q.* FROM questions q JOIN executions e ON e.id=q.execution_id WHERE q.id=? AND q.status='pending' AND e.finished_at IS NULL AND e.stop_requested=0",
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
        from .model_settings import active
        return {"global_instructions": db.setting("global_instructions", ""), "max_workers": 1,
                "model_provider": active(db)}

    static = Path(__file__).parent / "static"
    # Pin content and URLs to this process/release; never cache mutable aliases.
    assets = {}
    index_html = (static / "index.html").read_text()
    for path in static.rglob("*"):
        if path.suffix not in {".js", ".css"}:
            continue
        name = path.relative_to(static).as_posix()
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        assets[(digest, name)] = content
        index_html = index_html.replace(f'"/static/{name}"', f'"/static/assets/{digest}/{name}"')

    @app.get("/manifest.webmanifest")
    def manifest():
        return FileResponse(static / "manifest.webmanifest", media_type="application/manifest+json")

    @app.get("/static/assets/{digest}/{name:path}")
    def versioned_asset(digest: str, name: str):
        content = assets.get((digest, name))
        if content is None:
            raise HTTPException(404, "Unknown asset version")
        return Response(content, media_type="text/css" if name.endswith(".css") else "text/javascript",
                        headers={"Cache-Control": "public, max-age=31536000, immutable"})

    app.mount("/static", StaticFiles(directory=static), name="static")
    return app
