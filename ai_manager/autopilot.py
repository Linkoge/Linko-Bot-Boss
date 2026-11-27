"""Autopilot: an autonomous, goal-driven manager loop for a dedicated machine (a VM). Off by default. docs/AUTOPILOT.md.

Two locks, both local to the machine and both outside this application's reach:
- config lock: AI_MANAGER_AUTOPILOT=full in the process environment (this machine's own env file). Read once from the
  environment (config.Settings); there is no database setting, API or UI control that sets it, and nothing syncs it.
- OS lock: passwordless sudo for this user from a sudoers drop-in the owner installs. Detected with `sudo -n true`.
Both are set by scripts/autopilot_setup.sh, which the owner runs once with sudo; the app only shows its command line.
On top, a per-machine soft switch (the PAUSED setting, local DB, never synced) can stop Autopilot; it cannot start it.
Level: full = both, partial = exactly one, off = neither. A restricted instance is always off and never probes sudo.
Only full runs the loop. Goals may be saved while the config lock is set (partial or full).

Loop per active goal (Engine): plan ONE step (Opus, low; medium for the first plan and after 2 consecutive failed
steps) -> delegate it to a queue worker (normal pipeline: checks, merge, deploy) -> verify (desktop screenshot against
the reference images with a vision model, or the worker report) and score every "done when" item -> journal -> next.
Done at >= PASS of the checklist plus a final Opus medium check. No improvement in STALL_ROUNDS rounds: a new approach
(logged); a second stall pauses and asks the owner. The daily token budget and the deadline pause it too.
Wake-ups: a finished task wakes the goal at once; while a step runs, every CHECK_SECONDS a non-AI probe looks at the
task (process alive, events, output growth): progressing -> sleep (zero tokens), unclear -> Haiku decides, stuck ->
the Opus manager decides (wait or cancel the step).
Checkpoints: a non-forcing GitHub backup push when the score improves; etckeeper commits /etc before and after each
step (when installed); the optional AI_MANAGER_SNAPSHOT_CMD runs at goal start and before risky steps.
"""

import asyncio
import getpass
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from .db import new_id

ENV, ENV_VALUE = "AI_MANAGER_AUTOPILOT", "full"
SUDOERS = "/etc/sudoers.d/linko-autopilot"
UNIT = "linko-bot-manager"
OVERRIDE = f"~/.config/systemd/user/{UNIT}.service.d/autopilot.conf"
PAUSED = "autopilot_paused"  # the owner's global pause button; it can only stop work, never enable anything
KEY_PREFIX = "autopilot:"  # tasks.request_key of autopilot steps: their results go to the engine, not the chat
CHECK_SECONDS = 20 * 60
STALL_ROUNDS = 4
PASS = 0.9
PROBE_TTL = 30
STEP_TIMEOUT, CHECK_TIMEOUT = 600, 180
MAX_CHECKLIST, MAX_IMAGES, IMAGE_BYTES = 30, 6, 8 * 1024 * 1024
IMAGE_TYPES = {b"\x89PNG\r\n\x1a\n": "png", b"\xff\xd8\xff": "jpg", b"RIFF": "webp"}
STATUSES = ("active", "paused", "waiting", "stopped", "done")
APPROVAL = "NEEDS OWNER APPROVAL:"
# Even on full autopilot these wait for the owner (rule 8). Everything inside this machine is the loop's own call.
APPROVALS = (
    "spending money or using paid services",
    "publishing or anything outward-facing (posts, emails, public repositories, releases)",
    "touching Main or any other machine",
    "the owner's real accounts or secrets",
    "changing autopilot's own rules or limits (its env lines, the sudoers drop-in, budgets, this list)",
)
# Backstop for the planner's own "risky" call: steps that can leave the machine unbootable or without a desktop.
RISKY = re.compile(r"display manager|\b(gdm3?|lightdm|sddm|xorg|wayland|grub|initramfs|bootloader|boot config|"
                   r"kernel|fstab|systemd-boot)\b|\b(apt(-get)?|dpkg|snap|dnf|pacman)\s+(remove|purge|-r|autoremove)\b"
                   r"|package removal|uninstall", re.I)
WORKER_RULES = (
    "Autopilot step on the owner's dedicated machine: you may use sudo for anything inside this machine "
    "(packages, services, desktop, system configuration).",
    "Do exactly this one step, then report what you changed, what you observed and how to verify it.",
    f"Do NOT do any of the following; instead stop and write one line '{APPROVAL} <what and why>': "
    + "; ".join(APPROVALS) + ".",
)
log = logging.getLogger(__name__)


# Locks ----------------------------------------------------------------------------------------------------------------

def level_of(config, os_lock, restricted=False):
    if restricted:
        return "off"
    return "full" if config and os_lock else "partial" if config or os_lock else "off"


def sudo_works(run=subprocess.run):
    try:
        return run(["sudo", "-n", "true"], capture_output=True, timeout=5, stdin=subprocess.DEVNULL).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def no_new_privileges():
    try:
        return bool(re.search(r"^NoNewPrivs:\s*1$", Path("/proc/self/status").read_text(), re.M))
    except OSError:
        return False


class Locks:
    def __init__(self, settings, run=subprocess.run, clock=time.monotonic):
        self.settings, self.run, self.clock = settings, run, clock
        self.cached = None

    def config(self):
        return not self.settings.restricted and self.settings.autopilot == ENV_VALUE

    def os_lock(self):
        if self.settings.restricted:
            return False
        now = self.clock()
        if self.cached is None or now - self.cached[0] > PROBE_TTL:
            self.cached = (now, sudo_works(self.run))
        return self.cached[1]

    def level(self):
        return level_of(self.config(), self.os_lock(), self.settings.restricted)

    def status(self):
        config, os_lock = self.config(), self.os_lock()
        return {"level": level_of(config, os_lock, self.settings.restricted), "config": config, "os": os_lock,
                "restricted": self.settings.restricted, "no_new_privileges": no_new_privileges()}


def env_line_present(path):
    try:
        return any(line.strip() == f"{ENV}={ENV_VALUE}" for line in Path(path).read_text().splitlines())
    except OSError:
        return False


DEFAULT_ENV_FILE = "~/.config/linko-bot-manager.env"
ROLE_ENV = "AI_MANAGER_MACHINE_ROLE"  # =main on the owner's Main: no setup command there, and the script refuses


def is_main():
    return os.getenv(ROLE_ENV, "").strip().lower() == "main"


def on(db):
    """The per-machine soft switch (local DB, never synced). It can only stop work: running still needs both locks."""
    return not db.setting(PAUSED)


def worker_user():
    try:  # the separate worker user (README "How work runs") gets the same sudo rule so workers can use sudo
        from . import isolation
        found = isolation.detect(isolation.CONFIG)
        return found.user if found else None
    except Exception:
        return None


def setup_command(settings, user=None, worker=None):
    """The one line the owner pastes in a terminal on this machine (scripts/autopilot_setup.sh). Never run by the app."""
    from .config import CODE_ROOT
    user = user or getpass.getuser()
    argv = ["sudo", "bash", str(CODE_ROOT / "scripts" / "autopilot_setup.sh"), "--user", user]
    if Path(settings.env_file).expanduser() != Path(DEFAULT_ENV_FILE).expanduser():
        argv += ["--env-file", str(settings.env_file)]
    if worker:
        argv += ["--worker", worker]
    return shlex.join(argv)


def setup_view(settings, locks, db):
    status = locks.status()
    main = is_main()
    return {"set_up": status["level"] == "full", "on": on(db), "main": main,
            "command": "" if main or settings.restricted else setup_command(settings, worker=worker_user())}


def setup_note(settings, locks):
    """Manager context when the owner mentions Autopilot on a machine where it is not set up yet."""
    if settings.restricted:
        return None
    if is_main():
        return {"setup": "This is the owner's Main machine: Autopilot is not for it (use a dedicated machine)."}
    return {"level": locks.level(), "setup": (
        "Autopilot is not set up on this machine. If the owner asks to set it up, reply with exactly this one command "
        "to paste in a terminal on this machine (you cannot run sudo before setup, so do not try it yourself); it asks "
        "for confirmation, and --remove undoes it:"), "command": setup_command(settings, worker=worker_user())}


# Goals ----------------------------------------------------------------------------------------------------------------

@dataclass
class Loop:
    """A goal's loop state (autopilot_goals.loop). Every transition goes through decide()/confirmed()."""
    phase: str = "plan"  # plan -> working -> plan ... -> confirm -> done
    best: float = 0.0
    stall: int = 0  # rounds without improvement since the last improvement or new approach
    stalls: int = 0  # stall periods reached (1 = a new approach is being tried)
    failures: int = 0  # consecutive failed steps
    approach: int = 1
    started: bool = False  # goal-start snapshot taken
    wake: bool = True  # False once done: the manager has switched its own wake-ups off
    usage_day: str = ""
    usage: int = 0  # estimated tokens spent on usage_day (manager calls + worker turns)
    probe: dict | None = None  # the last non-AI progress check of the running step
    step: str = ""
    risky: bool = False
    note: str = ""

    @classmethod
    def load(cls, text):
        data = json.loads(text or "{}")
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def dump(self):
        return json.dumps(asdict(self))


def today(now):
    return time.strftime("%Y-%m-%d", time.localtime(now))


def spent(loop, now):
    return loop.usage if loop.usage_day == today(now) else 0


def charge(loop, tokens, now):
    if loop.usage_day != today(now):
        loop.usage_day, loop.usage = today(now), 0
    loop.usage += max(0, int(tokens))


def over_budget(goal, loop, now):
    if goal.get("deadline") and now > goal["deadline"]:
        return "deadline reached"
    if goal.get("daily_budget") and spent(loop, now) >= goal["daily_budget"]:
        return f"daily budget of {goal['daily_budget']} tokens reached"
    return ""


def decide(loop, score, failed, goal=None, now=None):
    """One verified round -> (decision, improved). Decisions: pause_budget, confirm, new_approach, pause_stalled,
    continue. No round cap while the score improves."""
    loop.failures = loop.failures + 1 if failed else 0
    improved = score > loop.best + 1e-9
    if improved:
        loop.best, loop.stall = score, 0
    else:
        loop.stall += 1
    if goal is not None and over_budget(goal, loop, now or time.time()):
        loop.phase = "plan"
        return "pause_budget", improved
    if score >= PASS:
        loop.phase = "confirm"
        return "confirm", improved
    loop.phase = "plan"
    if loop.stall >= STALL_ROUNDS:
        if loop.stalls == 0:
            loop.stalls, loop.stall, loop.approach = 1, 0, loop.approach + 1
            return "new_approach", improved
        return "pause_stalled", improved
    return "continue", improved


def confirmed(loop, ok):
    """The final Opus medium check: done (wake-ups off), or back to planning (counts as a round without progress)."""
    if ok:
        loop.phase, loop.wake = "done", False
        return "done"
    loop.phase, loop.stall = "plan", loop.stall + 1
    return "continue"


def effort_for(purpose, loop, rounds):
    """Manager effort: low by default; medium for the first plan, after 2 consecutive failures and the final check."""
    if purpose == "confirm" or (purpose == "plan" and (rounds == 0 or loop.failures >= 2)):
        return "medium"
    return "low"


def probe(db, goal, loop, now, alive=None):
    """Cheap non-AI progress check of the goal's running step: finished, progressing, unclear or stuck."""
    alive = alive or pid_alive
    task = db.one("SELECT state,held,lease_owner FROM tasks WHERE id=?", (goal.get("task_id"),))
    if not task:
        return "stuck", {}
    if task["state"] in ("done", "failed"):
        return "finished", {}
    if task["held"]:
        return "stuck", {}  # someone held it: a decision is needed
    if task["state"] == "queued":
        return "progressing", {}  # waiting its turn in the project queue costs nothing
    row = db.one("""SELECT e.id,e.pid,length(e.output) AS output,
        (SELECT max(seq) FROM events v WHERE v.task_id=e.task_id) AS seq
        FROM executions e WHERE e.task_id=? ORDER BY e.started_at DESC LIMIT 1""", (goal["task_id"],))
    if not row:
        return "unclear", {}
    seen = {"execution": row["id"], "output": row["output"] or 0, "seq": row["seq"] or 0, "at": now}
    last = loop.probe or {}
    if row["pid"] and not alive(row["pid"]):
        return "stuck", seen
    if last.get("execution") != row["id"]:
        return "progressing", seen  # a new execution (repair round) started since the last check
    if seen["seq"] > last.get("seq", 0) or seen["output"] > last.get("output", 0):
        return "progressing", {**seen, "quiet": 0}
    quiet = last.get("quiet", 0) + 1
    return ("stuck" if quiet >= 2 else "unclear"), {**seen, "quiet": quiet}


def pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except PermissionError:
        return True  # another user's (the worker user's) live process
    except (OSError, ValueError):
        return False


def parse_reply(text):
    """The first JSON object in a model reply."""
    text = text or ""
    start = text.find("{")
    while start != -1:
        depth = 0
        for index in range(start, len(text)):
            depth += {"{": 1, "}": -1}.get(text[index], 0)
            if depth == 0:
                try:
                    value = json.loads(text[start:index + 1])
                    if isinstance(value, dict):
                        return value
                except ValueError:
                    pass
                break
        start = text.find("{", start + 1)
    raise ValueError("the model reply has no JSON object")


def image_kind(data):
    for magic, suffix in IMAGE_TYPES.items():
        if data.startswith(magic) and (suffix != "webp" or data[8:12] == b"WEBP"):
            return suffix
    return None


class Goals:
    def __init__(self, db, registry=None, data_dir=None):
        self.db, self.registry = db, registry
        self.root = Path(data_dir or db.path.parent) / "autopilot"

    @staticmethod
    def clean(title, description, checklist, daily_budget, deadline):
        title, description = (title or "").strip(), (description or "").strip()
        if not 1 <= len(title) <= 200:
            raise ValueError("A goal needs a title of 1-200 characters")
        if len(description) > 8000:
            raise ValueError("The description must be at most 8000 characters")
        if isinstance(checklist, str):
            checklist = checklist.splitlines()
        items = [" ".join(str(item).split()) for item in checklist or [] if str(item).strip()]
        if not 1 <= len(items) <= MAX_CHECKLIST or any(len(item) > 300 for item in items):
            raise ValueError(f"'Done when' needs 1-{MAX_CHECKLIST} items of at most 300 characters")
        if daily_budget in ("", None):
            daily_budget = None
        elif not isinstance(daily_budget, int) or isinstance(daily_budget, bool) or not 1000 <= daily_budget <= 10**9:
            raise ValueError("The daily budget is a whole number of tokens between 1000 and 1000000000")
        if deadline in ("", None):
            deadline = None
        elif isinstance(deadline, str):
            try:
                deadline = time.mktime(time.strptime(deadline.strip()[:10], "%Y-%m-%d")) + 86399
            except ValueError:
                raise ValueError("The deadline is a date: YYYY-MM-DD") from None
        elif not isinstance(deadline, (int, float)) or isinstance(deadline, bool):
            raise ValueError("The deadline is a date: YYYY-MM-DD")
        return title, description, items, daily_budget, deadline

    def create(self, project_id, title, description="", checklist=(), daily_budget=None, deadline=None, source="panel"):
        title, description, items, daily_budget, deadline = self.clean(title, description, checklist, daily_budget,
                                                                        deadline)
        if not project_id or not self.db.one("SELECT 1 FROM projects WHERE id=? AND status!='removed'", (project_id,)):
            raise ValueError("Choose the project whose workers carry out the goal")
        goal_id, now = new_id(), time.time()
        with self.db.transaction() as conn:
            conn.execute("""INSERT INTO autopilot_goals(id,project_id,title,description,checklist,daily_budget,deadline,
                status,loop,next_wake,created_at,updated_at) VALUES(?,?,?,?,?,?,?,'active',?,?,?,?)""",
                         (goal_id, project_id, title, description, json.dumps(items), daily_budget, deadline,
                          Loop().dump(), now, now, now))
            self.note(conn, goal_id, 0, "created", f"Goal created from the {source}", "", None, None)
            self.db.event(conn, "autopilot.goal_created", {"goal_id": goal_id, "source": source}, project_id)
        return self.view(goal_id)

    def add_image(self, goal_id, name, data):
        goal = self.get(goal_id)
        suffix = image_kind(data or b"")
        if not suffix:
            raise ValueError("Reference images must be PNG, JPEG or WebP")
        if len(data) > IMAGE_BYTES:
            raise ValueError("A reference image must be at most 8 MB")
        images = json.loads(goal["images"])
        if len(images) >= MAX_IMAGES:
            raise ValueError(f"A goal keeps at most {MAX_IMAGES} reference images")
        folder = self.root / goal_id
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = folder / f"reference-{len(images) + 1}.{suffix}"
        path.write_bytes(data)
        os.chmod(path, 0o600)
        images.append({"name": Path(name or path.name).name[:120], "path": str(path)})
        with self.db.transaction() as conn:
            conn.execute("UPDATE autopilot_goals SET images=?,updated_at=? WHERE id=?",
                         (json.dumps(images), time.time(), goal_id))
        return self.view(goal_id)

    def get(self, goal_id):
        goal = self.db.one("SELECT * FROM autopilot_goals WHERE id=?", (goal_id,))
        if not goal:
            raise ValueError("Unknown goal")
        return goal

    def control(self, goal_id, op, reason=""):
        goal = self.get(goal_id)
        now = time.time()
        if op == "pause" and goal["status"] in ("active", "waiting"):
            status, wake = "paused", goal["next_wake"]
        elif op == "resume" and goal["status"] in ("paused", "waiting"):
            status, wake = "active", now
        elif op == "stop" and goal["status"] not in ("stopped", "done"):
            status, wake = "stopped", None
        else:
            raise ValueError(f"A {goal['status']} goal cannot {op}")
        with self.db.transaction() as conn:
            loop = Loop.load(goal["loop"])
            if op == "resume":  # the owner answered: fresh counters for stall and budget-free rounds
                loop.stall = loop.stalls = 0
            conn.execute("UPDATE autopilot_goals SET status=?,next_wake=?,pause_reason=?,loop=?,updated_at=? WHERE id=?",
                         (status, wake, reason if status != "active" else "", loop.dump(), now, goal_id))
            self.note(conn, goal_id, goal["rounds"], op, reason or f"Owner chose {op}", "", None, None)
        return self.view(goal_id)

    @staticmethod
    def note(conn, goal_id, round_, kind, done, result, score, scores, next_idea="", model="", effort="", tokens=0):
        conn.execute("""INSERT INTO autopilot_journal(goal_id,round,kind,done,result,score,scores,next_idea,model,effort,
            tokens,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (goal_id, round_, kind, done[:4000], (result or "")[:4000], score,
                      json.dumps(scores) if scores is not None else None, (next_idea or "")[:2000], model or "",
                      effort or "", int(tokens or 0), time.time()))

    def journal(self, goal_id, limit=20):
        self.get(goal_id)
        rows = self.db.all("SELECT * FROM autopilot_journal WHERE goal_id=? ORDER BY id DESC LIMIT ?",
                           (goal_id, max(1, min(int(limit), 200))))
        for row in rows:
            row["scores"] = json.loads(row["scores"]) if row["scores"] else None
        return rows

    def view(self, goal_id):
        goal = self.get(goal_id)
        loop = Loop.load(goal["loop"])
        last = self.db.one("SELECT kind,done,result,score,next_idea,created_at FROM autopilot_journal WHERE goal_id=? "
                           "ORDER BY id DESC LIMIT 1", (goal_id,))
        return {**{k: goal[k] for k in ("id", "project_id", "title", "description", "daily_budget", "deadline",
                                         "status", "score", "rounds", "task_id", "next_wake", "pause_reason",
                                         "created_at", "updated_at")},
                "checklist": json.loads(goal["checklist"]),
                "images": [{"name": i["name"]} for i in json.loads(goal["images"])],
                "phase": loop.phase, "approach": loop.approach, "wake": loop.wake,
                "used_today": spent(loop, time.time()), "last_journal": last}

    def list(self, project_id=None):
        rows = self.db.all("SELECT id FROM autopilot_goals WHERE ? IS NULL OR project_id=? "
                           "ORDER BY status IN ('stopped','done'), updated_at DESC LIMIT 50", (project_id, project_id))
        return [self.view(row["id"]) for row in rows]


def context(goals, locks, project_id=None):
    """The manager's view (chat context, only on a machine whose config lock is set)."""
    return {"level": locks.level(),
            "note": "autopilot_goal creates/controls owner goals; the loop runs them on full only. Still needs the "
                    "owner even on full: " + "; ".join(APPROVALS) + ".",
            "goals": [{k: g[k] for k in ("id", "title", "status", "score", "rounds", "phase")}
                      for g in goals.list(project_id)[:8]]}


AUTOPILOT_ACTION = "autopilot_goal"


def extend_tool(tool):
    """The manager tool on a machine whose config lock is set: one more action. Elsewhere the tool is unchanged."""
    import copy
    tool = copy.deepcopy(tool)
    properties = tool["inputSchema"]["properties"]
    properties["action"]["enum"] = [*properties["action"]["enum"], AUTOPILOT_ACTION]
    properties["goal"] = {"type": "object", "description": (
        "autopilot_goal, only when the owner typed it: {op: create|list|pause|resume|stop, goal_id, project_id, title, "
        "description, done_when: [checklist items], daily_budget (tokens), deadline (YYYY-MM-DD)}. Images the owner "
        "attached to this message become the goal's reference images.")}
    tool["description"] += (" Autopilot is configured on this machine: autopilot_goal saves or controls an owner "
                             "goal that the autopilot loop pursues (context autopilot).")
    return tool


def chat_action(goals, locks, args, selected_project, internal, attachments=()):
    """The manager's autopilot_goal action: the owner's own message only, never a result notice."""
    if internal:
        raise ValueError("autopilot_goal needs the owner's own message; report and wait")
    if not locks.config():
        raise ValueError("Autopilot is off on this machine")
    goal = args.get("goal") or {}
    op = goal.get("op", "create")
    if op == "list":
        return {"goals": goals.list(selected_project)}
    if op in ("pause", "resume", "stop"):
        found = goals.get(goal.get("goal_id", ""))
        if selected_project and found["project_id"] != selected_project:
            raise ValueError("Goal belongs to another project")
        return goals.control(found["id"], op, "Owner asked in chat")
    if op != "create":
        raise ValueError("goal.op is create, list, pause, resume or stop")
    project_id = goal.get("project_id") or selected_project
    if selected_project and project_id != selected_project:
        raise ValueError("Goal belongs to another project")
    created = goals.create(project_id, goal.get("title", ""), goal.get("description", ""), goal.get("done_when", []),
                           goal.get("daily_budget"), goal.get("deadline"), source="chat")
    for item in attachments:
        if item.get("native_image", item.get("mime", "").startswith("image/")) and item.get("path"):
            try:
                created = goals.add_image(created["id"], item.get("name", ""), Path(item["path"]).read_bytes())
            except (OSError, ValueError) as error:
                log.info("autopilot.image_skipped reason=%s", error)
    return created


# Checkpoints ----------------------------------------------------------------------------------------------------------

def etckeeper(message, run=subprocess.run, which=None):
    """Commit /etc with etckeeper (through sudo) when installed. Returns a journal line."""
    if not (which or shutil.which)("etckeeper"):
        return "etckeeper not installed; /etc not committed"
    try:
        result = run(["sudo", "-n", "etckeeper", "commit", message[:200]], capture_output=True, text=True, timeout=120,
                     stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as error:
        return f"etckeeper commit failed: {error}"
    text = (result.stdout + result.stderr).strip()
    if result.returncode == 0:
        return "etckeeper: /etc committed"
    return "etckeeper: nothing to commit" if "nothing to commit" in text else f"etckeeper failed: {text[-300:]}"


def snapshot(command, goal_id, label, run=subprocess.run):
    """The owner's VM snapshot hook: the command's own argv plus the goal id and a label. No shell."""
    if not command:
        return "no snapshot hook configured (AI_MANAGER_SNAPSHOT_CMD); continuing without a snapshot"
    try:
        result = run([*shlex.split(command), goal_id, label], capture_output=True, text=True, timeout=900,
                     stdin=subprocess.DEVNULL)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        return f"snapshot hook failed: {error}"
    if result.returncode:
        return f"snapshot hook failed (exit {result.returncode}): {(result.stderr or result.stdout).strip()[-300:]}"
    return f"snapshot taken ({label})"


def desktop_screenshot(settings, path, run=subprocess.run, which=None):
    """The real desktop of this machine (its X/VNC display). Returns the path, or None with no tool or display."""
    which = which or shutil.which
    env = {**os.environ, **({"DISPLAY": settings.autopilot_display} if settings.autopilot_display else {})}
    env.setdefault("DISPLAY", ":0")
    candidates = []
    if settings.screenshot_cmd:
        candidates.append([*shlex.split(settings.screenshot_cmd), str(path)])
    for tool, argv in (("import", ["import", "-window", "root", str(path)]), ("scrot", ["scrot", "-o", str(path)]),
                       ("grim", ["grim", str(path)]), ("gnome-screenshot", ["gnome-screenshot", "-f", str(path)])):
        if which(tool):
            candidates.append(argv)
    for argv in candidates:
        try:
            if run(argv, capture_output=True, timeout=60, env=env, stdin=subprocess.DEVNULL).returncode == 0 \
                    and Path(path).is_file() and Path(path).stat().st_size:
                return Path(path)
        except (OSError, ValueError, subprocess.SubprocessError):
            continue
    return None


# Engine ---------------------------------------------------------------------------------------------------------------

PLAN = ("You are the Autopilot manager of a dedicated machine. Choose exactly ONE next step toward the goal, small "
        "enough for one worker task. Reply with one JSON object only: {\"step\": worker objective, \"acceptance\": how "
        "the worker proves it, \"risky\": true if it changes the desktop/display manager, boot configuration or removes "
        "packages, \"needs_owner\": \"\" or what needs the owner's approval, \"why\": one line}.")
VERIFY = ("You verify an Autopilot goal. Score every 'done when' item strictly from the evidence (the worker report; "
          "when images are attached: the first ones are the owner's reference images, the LAST one is a screenshot of "
          "the machine's real desktop now). Reply with one JSON object only: {\"items\": [{\"pass\": true|false, "
          "\"note\": short}] in checklist order, \"summary\": one line, \"next\": the most useful next idea}.")
CONFIRM = ("Final check of an Autopilot goal that scored at least 90%. Be skeptical: confirm only if the evidence shows "
           "the goal is really done. Reply with one JSON object only: {\"done\": true|false, \"reason\": one line}.")
TRIAGE = ("A worker step has shown no new output or events for 20 minutes. From the facts, reply with one JSON object "
          "only: {\"verdict\": \"progressing\"|\"stuck\", \"reason\": one line}.")
STUCK = ("A worker step for an Autopilot goal looks stuck. Decide: wait longer or cancel it and plan another step. Reply "
         "with one JSON object only: {\"decision\": \"wait\"|\"cancel\", \"reason\": one line}.")


def latest(server, family):
    found = [m["model"] for m in getattr(server, "catalog", None) or [] if not m.get("hidden")
             and family in m["model"].lower()]
    return max(found, key=lambda name: ([int(n) for n in re.findall(r"\d+", name)], name)) if found else None


class Engine:
    """Drives every active goal on a full-autopilot machine. All model calls and side effects are injectable."""

    def __init__(self, db, registry, settings, server=None, pipeline=None, core=None, locks=None, ask=None,
                 delegate=None, cancel=None, screenshot=None, run=subprocess.run, clock=time.time, workspace=None):
        self.db, self.registry, self.settings, self.server = db, registry, settings, server
        self.pipeline, self.core, self.run, self.clock = pipeline, core, run, clock
        self.locks = locks or Locks(settings)
        self.goals = Goals(db, registry, settings.data_dir)
        self.ask = ask or self.model_ask
        self.delegate = delegate or self.queue_step
        self.cancel = cancel or self.cancel_step
        self.screenshot = screenshot or (lambda path: desktop_screenshot(settings, path, run))
        self.workspace = workspace or settings.data_dir / "autopilot-workspace"
        self.jobs = {}

    # scheduling

    def runs(self):
        return self.locks.config() and self.locks.level() == "full" and not self.db.setting(PAUSED)

    def due(self, now):
        return self.db.all("""SELECT g.id FROM autopilot_goals g LEFT JOIN tasks t ON t.id=g.task_id
            WHERE g.status='active' AND (g.next_wake<=? OR (json_extract(g.loop,'$.phase')='working'
                AND t.state IN ('done','failed') AND t.lease_owner IS NULL))
            ORDER BY g.next_wake""", (now,))

    async def tick(self):
        if not self.locks.config():
            return  # the default everywhere: no query, no probe, no tokens
        if not self.runs():
            return
        for row in await asyncio.to_thread(self.due, self.clock()):
            if row["id"] not in self.jobs:
                job = asyncio.create_task(self.wake(row["id"]))
                self.jobs[row["id"]] = job
                job.add_done_callback(lambda _, key=row["id"]: self.jobs.pop(key, None))

    async def close(self):
        for job in list(self.jobs.values()):
            job.cancel()
        await asyncio.gather(*self.jobs.values(), return_exceptions=True)

    async def wake(self, goal_id):
        try:
            goal = self.goals.get(goal_id)
            if goal["status"] != "active":
                return
            loop = Loop.load(goal["loop"])
            if loop.phase == "working":
                verdict, seen = await asyncio.to_thread(probe, self.db, goal, loop, self.clock())
                if verdict == "finished":
                    await self.verify(goal, loop)
                else:
                    await self.check(goal, loop, verdict, seen)
            elif loop.phase == "confirm":
                await self.confirm(goal, loop)
            else:
                await self.plan(goal, loop)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            log.exception("autopilot.wake_failed goal=%s", goal_id)
            with self.db.transaction() as conn:
                conn.execute("UPDATE autopilot_goals SET next_wake=?,updated_at=? WHERE id=?",
                             (self.clock() + 300, self.clock(), goal_id))
                Goals.note(conn, goal_id, None, "error", "Autopilot step failed; retrying in 5 minutes", str(error),
                           None, None)

    def save(self, goal, loop, journal=None, status=None, reason=None, **columns):
        """Persist loop state (and optional columns); never revives a goal the owner paused or stopped meanwhile."""
        now = self.clock()
        sets, params = ["loop=?", "updated_at=?"], [loop.dump(), now]
        for name, value in columns.items():
            sets.append(f"{name}=?")
            params.append(value)
        with self.db.transaction() as conn:
            current = conn.execute("SELECT status FROM autopilot_goals WHERE id=?", (goal["id"],)).fetchone()
            if not current or current["status"] != "active":
                if journal:
                    Goals.note(conn, goal["id"], *journal)
                return False
            if status:
                sets += ["status=?", "pause_reason=?"]
                params += [status, reason or ""]
                if status != "active":
                    self.db.event(conn, "autopilot.paused" if status in ("paused", "waiting") else "autopilot." + status,
                                  {"goal_id": goal["id"], "reason": reason or ""}, goal["project_id"])
                    self.tell_owner(conn, goal, status, reason, columns.get("score", goal["score"]))
            conn.execute(f"UPDATE autopilot_goals SET {','.join(sets)} WHERE id=?", (*params, goal["id"]))
            if journal:
                Goals.note(conn, goal["id"], *journal)
        return True

    @staticmethod
    def tell_owner(conn, goal, status, reason, score):
        """One plain line in the project's chat when a goal finishes or needs the owner (like auto mode's pause)."""
        conversation = conn.execute("SELECT id FROM conversations WHERE context=?", (goal["project_id"],)).fetchone()
        if not conversation:
            return
        if status == "done":
            text = f"Autopilot finished \"{goal['title']}\" (score {round(score * 100)}%)."
        else:
            text = (f"Autopilot {'is waiting on' if status == 'waiting' else 'paused'} \"{goal['title']}\": {reason}. "
                    "Resume it in Settings > Autopilot or tell me here.")
        conn.execute("INSERT INTO messages(id,conversation_id,role,content,status,created_at) "
                     "VALUES(?,?,'assistant',?,'completed',?)", ("autopilot-" + new_id(), conversation["id"], text,
                                                                 time.time()))

    # model calls

    def route(self, family, effort):
        model = latest(self.server, family) if self.server else None
        try:
            return self.server.route(model, effort)
        except ValueError:
            return self.server.route(model, None)

    async def model_ask(self, purpose, effort, text, images=()):
        await self.server.start()
        route = self.route("haiku" if purpose == "triage" else "opus", effort)
        self.workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
        from .office_chats import ask
        instructions = {"plan": PLAN, "verify": VERIFY, "confirm": CONFIRM, "triage": TRIAGE, "stuck": STUCK}[purpose]
        reply = await ask(self.server, self.workspace, instructions, text, route, CHECK_TIMEOUT, images)
        return reply, route.get("model", ""), route.get("effort", effort)

    async def call(self, loop, purpose, effort, facts, images=()):
        text = json.dumps(facts, ensure_ascii=False, default=str)
        reply, model, used = await self.ask(purpose, effort, text, images)
        charge(loop, (len(text) + len(reply or "")) // 4 + 1500 * len(images), self.clock())
        return parse_reply(reply), model, used

    def facts(self, goal, loop):
        journal = self.db.all("SELECT round,kind,done,result,score,next_idea FROM autopilot_journal WHERE goal_id=? "
                              "ORDER BY id DESC LIMIT 6", (goal["id"],))
        return {"goal": {"title": goal["title"], "description": goal["description"],
                         "done_when": json.loads(goal["checklist"]),
                         "reference_images": len(json.loads(goal["images"]))},
                "round": goal["rounds"], "score": goal["score"], "best": loop.best, "approach": loop.approach,
                "consecutive_failures": loop.failures, "journal": list(reversed(journal)),
                "needs_owner_even_on_full": list(APPROVALS)}

    # phases

    async def plan(self, goal, loop):
        now = self.clock()
        if reason := over_budget(goal, loop, now):
            self.save(goal, loop, (goal["rounds"], "budget", "Paused: " + reason, "Progress saved", goal["score"], None),
                      status="paused", reason=reason)
            return
        notes = []
        if not loop.started:
            loop.started = True
            notes.append(await asyncio.to_thread(snapshot, self.settings.snapshot_cmd, goal["id"], "goal-start", self.run))
        effort = effort_for("plan", loop, goal["rounds"])
        facts = self.facts(goal, loop)
        if loop.note:
            facts["instruction"] = loop.note
        plan, model, effort = await self.call(loop, "plan", effort, facts)
        step = str(plan.get("step") or "").strip()
        if plan.get("needs_owner"):
            self.save(goal, loop, (goal["rounds"], "approval", step or "Planned step", str(plan["needs_owner"]),
                                   goal["score"], None, "", model, effort), status="waiting",
                      reason="Needs your approval: " + str(plan["needs_owner"])[:500])
            return
        if not step:
            raise ValueError("the plan has no step")
        loop.risky = bool(plan.get("risky")) or bool(RISKY.search(step))
        if loop.risky:
            notes.append(await asyncio.to_thread(snapshot, self.settings.snapshot_cmd, goal["id"], "risky-step", self.run))
        notes.append(await asyncio.to_thread(etckeeper, f"autopilot: before round {goal['rounds'] + 1}: {step[:120]}",
                                             self.run))
        task_id = await self.delegate(goal, step, str(plan.get("acceptance") or ""),
                                      f"{KEY_PREFIX}{goal['id']}:{goal['rounds'] + 1}:{loop.approach}")
        loop.phase, loop.step, loop.probe, loop.note = "working", step, None, ""
        self.save(goal, loop, (goal["rounds"] + 1, "step", step, " · ".join(notes), None, None,
                               str(plan.get("why") or ""), model, effort),
                  task_id=task_id, rounds=goal["rounds"] + 1, next_wake=self.clock() + CHECK_SECONDS)

    async def verify(self, goal, loop):
        task = self.db.one("SELECT state,blocker FROM tasks WHERE id=?", (goal["task_id"],))
        rows = self.db.all("SELECT id,output,usage FROM executions WHERE task_id=? ORDER BY started_at", (goal["task_id"],))
        with self.db.transaction() as conn:  # this step's result belongs to the loop, not to a chat notice
            conn.execute("UPDATE executions SET delivery='autopilot' WHERE task_id=? AND delivery='pending'",
                         (goal["task_id"],))
        for row in rows:
            charge(loop, worker_tokens(row["usage"]), self.clock())
        report = (rows[-1]["output"] if rows else "")[-6000:]
        failed = not task or task["state"] == "failed"
        after = await asyncio.to_thread(etckeeper, f"autopilot: after round {goal['rounds']}", self.run)
        if APPROVAL in report:
            reason = report[report.index(APPROVAL):].splitlines()[0][:500]
            self.save(goal, loop, (goal["rounds"], "approval", loop.step, reason, goal["score"], None),
                      status="waiting", reason=reason)
            return
        images = [i["path"] for i in json.loads(goal["images"]) if Path(i["path"]).is_file()]
        shot = None
        if images:
            folder = self.goals.root / goal["id"]
            folder.mkdir(parents=True, exist_ok=True, mode=0o700)
            shot = await asyncio.to_thread(self.screenshot, folder / f"desktop-{goal['rounds']}.png")
        facts = {**self.facts(goal, loop), "step": loop.step, "task_state": task and task["state"],
                 "task_reason": (task or {}).get("blocker", "")[:1500], "worker_report": report,
                 "desktop_screenshot": bool(shot)}
        result, model, effort = await self.call(loop, "verify", "low", facts, [*images, *([shot] if shot else [])])
        checklist = json.loads(goal["checklist"])
        items = [i if isinstance(i, dict) else {} for i in (result.get("items") or [])]
        scores = [{"item": item, "pass": bool(items[n].get("pass")) if n < len(items) else False,
                   "note": str(items[n].get("note", ""))[:300] if n < len(items) else "not scored"}
                  for n, item in enumerate(checklist)]
        score = round(sum(s["pass"] for s in scores) / len(scores), 4)
        decision, improved = decide(loop, score, failed, goal, self.clock())
        notes = [after] + ([] if shot or not images else ["no desktop screenshot available (no display/tool)"])
        if improved and self.pipeline is not None:
            try:
                self.pipeline.backups.request(goal["project_id"])
                notes.append("checkpoint: GitHub backup push requested")
            except Exception as error:
                notes.append(f"checkpoint push not requested: {error}")
        summary = str(result.get("summary") or "")[:1000]
        journal = (goal["rounds"], "verify", loop.step, " · ".join([f"task {task and task['state']}", summary, *notes]),
                   score, scores, str(result.get("next") or ""), model, effort)
        columns = {"score": score, "next_wake": self.clock()}
        if decision == "pause_budget":
            self.save(goal, loop, journal, status="paused", reason=over_budget(goal, loop, self.clock()), **columns)
        elif decision == "pause_stalled":
            self.save(goal, loop, journal, status="paused",
                      reason=f"No progress in {STALL_ROUNDS} rounds, twice (a new approach was tried): your input is "
                             "needed", **columns)
        else:
            if decision == "new_approach":
                loop.note = (f"No improvement in {STALL_ROUNDS} rounds: abandon the current approach and try a "
                             f"different one (approach {loop.approach}).")
                journal = (*journal[:6], journal[6] + f" · New approach {loop.approach}: no improvement in "
                           f"{STALL_ROUNDS} rounds", *journal[7:])
            self.save(goal, loop, journal, **columns)

    async def confirm(self, goal, loop):
        images = [i["path"] for i in json.loads(goal["images"]) if Path(i["path"]).is_file()]
        last = self.db.one("SELECT scores FROM autopilot_journal WHERE goal_id=? AND kind='verify' ORDER BY id DESC "
                           "LIMIT 1", (goal["id"],))
        shot = None
        if images:
            shot = await asyncio.to_thread(self.screenshot, self.goals.root / goal["id"] / "desktop-final.png")
        facts = {**self.facts(goal, loop), "scores": json.loads(last["scores"]) if last and last["scores"] else None}
        result, model, effort = await self.call(loop, "confirm", effort_for("confirm", loop, goal["rounds"]), facts,
                                                [*images, *([shot] if shot else [])])
        outcome = confirmed(loop, result.get("done") is True)
        journal = (goal["rounds"], "done" if outcome == "done" else "confirm",
                   "Final check", str(result.get("reason") or "")[:1000], goal["score"], None, "", model, effort)
        if outcome == "done":
            self.save(goal, loop, journal, status="done", reason="", next_wake=None)
        else:
            self.save(goal, loop, journal, next_wake=self.clock())

    async def check(self, goal, loop, verdict, seen):
        """The 20-minute wake-up of a running step: zero tokens unless the cheap probe is unsure or the step is stuck."""
        loop.probe = seen or loop.probe
        if verdict == "unclear":
            result, *_ = await self.call(loop, "triage", "low", {"step": loop.step, "probe": seen})
            verdict = "stuck" if result.get("verdict") == "stuck" else "progressing"
        if verdict == "stuck":
            result, model, effort = await self.call(loop, "stuck", "low", {**self.facts(goal, loop), "step": loop.step,
                                                                           "probe": seen})
            if result.get("decision") == "cancel":
                await self.cancel(goal["task_id"])
                self.save(goal, loop, (goal["rounds"], "cancel", loop.step, str(result.get("reason") or ""), None,
                                       None, "", model, effort), next_wake=self.clock())
                return
        self.save(goal, loop, next_wake=self.clock() + CHECK_SECONDS)

    # default side effects

    async def queue_step(self, goal, step, acceptance, request_key):
        from .core import Tasks
        from .model_settings import delegation
        route = None
        if self.server is not None:
            await self.server.start()
            route = delegation(self.db, self.server, project_id=goal["project_id"])
        objective = f"Autopilot goal: {goal['title']}\n\nThis step: {step}"
        task = await asyncio.to_thread(
            Tasks(self.db).add, goal["project_id"], objective[:8000], None, request_key, acceptance=acceptance[:12000],
            selected={"constraints": list(WORKER_RULES)}, model_route=route, reuse_objective=False)
        return task["id"]

    async def cancel_step(self, task_id):
        if self.core is not None:
            await self.core.control(task_id, "cancel")


def worker_tokens(usage):
    try:
        turn = (json.loads(usage or "{}") or {}).get("turn") or {}
    except ValueError:
        return 0
    if isinstance(turn.get("totalTokens"), int):
        return turn["totalTokens"]
    return sum(v for k, v in turn.items() if isinstance(v, int) and k in ("inputTokens", "outputTokens"))
