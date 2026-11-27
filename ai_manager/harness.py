"""Switchable harnesses: which prompt, manager actions and autonomy a manager or worker runs under.

A harness is a folder with ``harness.json`` and a prompt file. The built-ins live in the repository's ``harnesses/``
(Linko manager, Zero, Coding, Zero worker); more can be dropped into ``AI_MANAGER_HARNESS_DIR`` (default ``<data>/harnesses``) and
appear in the picker once they validate. An invalid manifest is listed with its reason and never used.

A harness only shapes the prompt, narrows the manager's action list and picks an autonomy the application already
enforces. It can never add an action, remove RULES.md rules 1, 3 and 10 from the prompt, or change what code enforces:
restricted-instance rejections, owner-typed-only actions and the follow-up limits (orchestration/followups), the host
agent's own confirmation rules (scripts/emergency_repair.py), and the worker flow (worktree, checks, repair rounds;
the application merges and deploys, workers never do).
"""

import copy
import json
import os
import re
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .db import new_id

BUILTIN_DIR = Path(__file__).resolve().parents[1] / "harnesses"
BUILTIN_FOLDERS = ("linko-manager", "zero", "coding", "zero-worker")
DEFAULTS = {"manager": "linko", "worker": "coding"}
HARD_RULES = (1, 3, 10)  # always in every harness prompt
AUTONOMY = {
    "ask": ("Ask", "Report each worker result and wait for the owner; no automatic follow-ups or auto tasks."),
    "finish_request": ("Finish request", "Bounded automatic follow-ups that only finish the owner's request "
                       "(at most 3), plus auto-mode tasks while auto mode is on."),
    "rounds": ("Rounds", "Worker, inspector review, fix, up to a round limit. Not yet available."),
}
AVAILABLE_AUTONOMY = ("ask", "finish_request")
# Actions that operate deploys or the host: a harness enabling them must carry the whole RULES.md.
PRIVILEGED = frozenset({"deploy", "save_deploy_setup", "emergency_repair"})
KEYS = {
    "manager": {"id", "name", "role", "description", "prompt", "rules", "architecture_guide", "actions", "autonomy"},
    "worker": {"id", "name", "role", "description", "prompt", "rules"},
}
REQUIRED = {"manager": {"id", "name", "role", "description", "prompt", "actions", "autonomy"},
            "worker": {"id", "name", "role", "description", "prompt"}}
WORKER_FORBIDDEN = {"deploy", "push", "merge", "actions", "autonomy", "tools", "checks", "gate"}
ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")
MANIFEST_LIMIT, PROMPT_LIMIT, EXTERNAL_LIMIT = 20000, 60000, 50
SELECTION = "harness_selection:"  # + project id or "global"
TASK_EVENT = "task.harness"
# Every manager action the application has (the manager tool's enum); a harness can only narrow this list.
MANAGER_ACTIONS = ("deploy", "deployment_status", "save_deploy_setup", "emergency_repair", "repair_status",
                   "list_todos", "save_todo", "delegate", "continue", "hold", "release", "cancel", "read_document",
                   "list_files", "search_code", "screenshot", "read_memory", "read_project", "list_memory",
                   "list_history", "read_task", "read_result", "list_tasks", "read_history", "pause_project",
                   "resume_project", "set_auto_mode", "auto_summary")


@dataclass(frozen=True)
class Harness:
    id: str
    name: str
    role: str
    description: str
    prompt: str
    rules: str | tuple = "all"
    architecture_guide: bool = False
    actions: str | tuple = "all"
    autonomy: str | None = None
    source: str = "builtin"

    def instructions(self, restricted=False):
        """The thread's developer instructions: rules, then the harness prompt (and the architecture guide)."""
        from .codex import ARCHITECTURE_GUIDE, RESTRICTED_NOTE
        text = rules_text(self.role, self.rules) + "\n" + self.prompt
        if self.architecture_guide:
            text += "\n" + ARCHITECTURE_GUIDE
        if restricted and self.role == "manager":
            text += RESTRICTED_NOTE
        return text

    def public(self):
        value = {"id": self.id, "name": self.name, "role": self.role, "description": self.description,
                 "source": self.source, "rules": self.rules if self.rules == "all" else list(self.rules)}
        if self.role == "manager":
            value |= {"actions": self.actions if self.actions == "all" else list(self.actions),
                      "autonomy": self.autonomy}
        return value


def rule_items(text):
    """Split a RULES.md view into its header and numbered items."""
    parts = re.split(r"(?m)^(?=\d+\. )", text)
    return parts[0], {int(part.split(".", 1)[0]): part for part in parts[1:]}


def rules_text(role, rules):
    """The whole role view of RULES.md, or the hard rules plus the listed ones (RULES.md stays the one source)."""
    from .codex import RULES, WORKER_RULES
    source = RULES if role == "manager" else WORKER_RULES
    if rules == "all":
        return source
    _, items = rule_items(source)
    wanted = sorted(set(HARD_RULES) | set(rules))
    return "# Rules\n\n" + "".join(items[n].rstrip("\n") + "\n" for n in wanted if n in items)


def load(folder, source="external"):
    """Validate one harness folder; ValueError with a plain reason when it must not be used."""
    folder = Path(folder)
    manifest = folder / "harness.json"
    if manifest.is_symlink() or not manifest.is_file():
        raise ValueError("harness.json is missing")
    if manifest.stat().st_size > MANIFEST_LIMIT:
        raise ValueError("harness.json is too large")
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ValueError("harness.json is not valid JSON") from None
    if not isinstance(data, dict):
        raise ValueError("harness.json must be a JSON object")
    role = data.get("role")
    if role not in KEYS:
        raise ValueError("role must be 'manager' or 'worker'")
    if role == "worker" and (bad := sorted(WORKER_FORBIDDEN & data.keys())):
        raise ValueError(f"a worker harness cannot set {', '.join(bad)}: workers never deploy, push or merge; "
                         "the application runs the checks, merges and deploys")
    if unknown := sorted(data.keys() - KEYS[role]):
        raise ValueError(f"unknown field(s) for a {role} harness: {', '.join(unknown)}")
    if missing := sorted(REQUIRED[role] - data.keys()):
        raise ValueError(f"missing field(s): {', '.join(missing)}")
    if not isinstance(data["id"], str) or not ID.fullmatch(data["id"]):
        raise ValueError("id must be 1-40 lowercase letters, digits, '-' or '_'")
    for key, limit in (("name", 40), ("description", 500)):
        if not isinstance(data[key], str) or not data[key].strip() or len(data[key]) > limit:
            raise ValueError(f"{key} must be text of 1-{limit} characters")
    name = data["prompt"]
    if not isinstance(name, str) or not name or "/" in name or "\\" in name or name.startswith("."):
        raise ValueError("prompt must be a file name inside the harness folder")
    path = folder / name
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"prompt file {name} is missing")
    if path.stat().st_size > PROMPT_LIMIT:
        raise ValueError(f"prompt file is larger than {PROMPT_LIMIT} bytes")
    try:
        prompt = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise ValueError("prompt file is not UTF-8 text") from None
    if not prompt.strip():
        raise ValueError("prompt file is empty")
    rules = data.get("rules", "all")
    if rules != "all":
        from .codex import RULES, WORKER_RULES
        numbers = rule_items(RULES if role == "manager" else WORKER_RULES)[1]
        if not isinstance(rules, list) or any(type(n) is not int or n not in numbers for n in rules):
            raise ValueError(f"rules must be 'all' or a list of RULES.md rule numbers ({min(numbers)}-{max(numbers)})")
        rules = tuple(sorted(set(rules)))
    fields = {}
    if role == "manager":
        actions, known = data["actions"], MANAGER_ACTIONS
        if actions != "all":
            if not isinstance(actions, list) or not actions or not all(isinstance(a, str) for a in actions):
                raise ValueError("actions must be 'all' or a non-empty list of manager actions")
            if extra := sorted(set(actions) - set(known)):
                raise ValueError(f"unknown manager action(s): {', '.join(extra)}; a harness cannot add actions")
            if PRIVILEGED & set(actions) and rules != "all":
                raise ValueError("enabling deploy, save_deploy_setup or emergency_repair needs rules 'all'")
            actions = tuple(a for a in known if a in actions)
        elif rules != "all":
            raise ValueError("actions 'all' includes deploy and emergency_repair, so it needs rules 'all'")
        autonomy = data["autonomy"]
        if autonomy not in AUTONOMY:
            raise ValueError(f"autonomy must be one of {', '.join(AUTONOMY)}")
        if autonomy not in AVAILABLE_AUTONOMY:
            raise ValueError(f"autonomy '{autonomy}' is not yet available")
        guide = data.get("architecture_guide", False)
        if not isinstance(guide, bool):
            raise ValueError("architecture_guide must be true or false")
        fields = {"actions": actions, "autonomy": autonomy, "architecture_guide": guide}
    return Harness(id=data["id"], name=data["name"].strip(), role=role, description=data["description"].strip(),
                   prompt=prompt, rules=rules, source=source, **fields)


@lru_cache(maxsize=None)
def builtins():
    return {h.id: h for h in (load(BUILTIN_DIR / folder, "builtin") for folder in BUILTIN_FOLDERS)}


def builtin(harness_id):
    return builtins()[harness_id]


def external_dir(db):
    configured = os.getenv("AI_MANAGER_HARNESS_DIR")
    return Path(configured).expanduser() if configured else Path(db.path).parent / "harnesses"


def catalog(db):
    """Every usable harness (built-ins first) and the invalid external ones with their reasons."""
    found, invalid = dict(builtins()), []
    root = external_dir(db)
    try:
        folders = sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))
    except OSError:
        folders = []
    for folder in folders[:EXTERNAL_LIMIT]:
        try:
            harness = load(folder)
            if harness.id in found:
                raise ValueError(f"id '{harness.id}' is already used by "
                                 f"{'a built-in' if found[harness.id].source == 'builtin' else 'another'} harness")
            found[harness.id] = harness
        except (OSError, ValueError) as error:
            invalid.append({"folder": folder.name, "path": str(folder), "error": str(error)})
    return found, invalid


def get(db, role, harness_id):
    found, invalid = catalog(db)
    harness = found.get(harness_id)
    if harness and harness.role == role:
        return harness
    broken = next((i for i in invalid if i["folder"] == harness_id), None)
    if broken:
        raise ValueError(f"{role.capitalize()} harness '{harness_id}' is invalid: {broken['error']}")
    raise ValueError(f"Unknown {role} harness '{harness_id}'; choose one of "
                     + ", ".join(h.id for h in found.values() if h.role == role))


def saved(db, project_id):
    value = db.setting(SELECTION + (project_id or "global")) or {}
    return value if isinstance(value, dict) else {}


def manager_turn(db, project_id):
    """(harness, autonomy) for the next manager turn in this chat scope; ValueError if the choice is unusable."""
    choice = saved(db, project_id)
    harness = get(db, "manager", choice.get("manager") or DEFAULTS["manager"])
    return harness, choice.get("autonomy") or harness.autonomy


def default_worker(db, project_id):
    return (saved(db, project_id).get("worker") if project_id else None) or DEFAULTS["worker"]


def manager_tool(harness, restricted=False):
    """The manager tool narrowed to the harness's actions; never wider than the application's own tool."""
    from .orchestration import MANAGER_TOOL, RESTRICTED_TOOL
    base = RESTRICTED_TOOL if restricted else MANAGER_TOOL
    if harness.actions == "all":
        return base
    tool = copy.deepcopy(base)
    properties = tool["inputSchema"]["properties"]
    enabled = [a for a in properties["action"]["enum"] if a in harness.actions]
    properties["action"]["enum"] = enabled
    if "save_deploy_setup" not in enabled:
        properties.pop("setup", None)
    tool["description"] = (f"Manager actions enabled by the {harness.name} harness: {', '.join(enabled)}. Reads are "
                           "read-only (no execution) and support offset pagination; delegate sends one complete "
                           "objective to the project's worker queue. model/effort apply to delegate when the worker "
                           "setting is Automatic.")
    return tool


def enabled_actions(harness, restricted=False):
    return set(manager_tool(harness, restricted)["inputSchema"]["properties"]["action"]["enum"])


def context(db, project_id):
    """What the manager needs to pick a worker harness per delegated task."""
    choice = saved(db, project_id)
    found, _ = catalog(db)
    value = {"manager": choice.get("manager") or DEFAULTS["manager"],
             "autonomy": choice.get("autonomy") or found.get(choice.get("manager") or DEFAULTS["manager"],
                                                            builtin(DEFAULTS["manager"])).autonomy,
             "worker_options": [{"id": h.id, "name": h.name, "description": h.description}
                                for h in found.values() if h.role == "worker"]}
    if project_id:
        value["worker_default"] = default_worker(db, project_id)
    return value


def state(db, project_id):
    """The picker: current choices, every option, the invalid harnesses and the autonomy levels."""
    found, invalid = catalog(db)
    choice = saved(db, project_id)
    value = {"project_id": project_id, "directory": str(external_dir(db)), "invalid": invalid,
             "options": {role: [h.public() for h in found.values() if h.role == role] for role in KEYS},
             "autonomy_levels": [{"id": key, "label": label, "description": text, "available": key in AVAILABLE_AUTONOMY}
                                 for key, (label, text) in AUTONOMY.items()]}
    for role in ("manager", "worker") if project_id else ("manager",):
        harness_id = choice.get(role) or DEFAULTS[role]
        try:
            value[role] = get(db, role, harness_id).public()
        except ValueError as error:
            value[role] = {"id": harness_id, "name": harness_id, "error": str(error)}
    if "error" not in value["manager"]:
        value["autonomy"] = choice.get("autonomy") or value["manager"]["autonomy"]
    return value


def save(db, project_id, body):
    """Update the scope's manager harness, autonomy and (per project) default worker harness.

    Returns the text of a chat notice when the next manager turn changes, else None."""
    if not isinstance(body, dict) or not body or body.keys() - {"manager", "worker", "autonomy"}:
        raise ValueError("Send manager, worker and/or autonomy")
    choice = dict(saved(db, project_id))
    before = manager_turn(db, project_id) if _usable(db, project_id) else (None, None)
    if "manager" in body:
        harness = get(db, "manager", body["manager"])
        if harness.id != (choice.get("manager") or DEFAULTS["manager"]) and "autonomy" not in body:
            choice.pop("autonomy", None)  # a new manager harness starts at its own autonomy
        choice["manager"] = harness.id
    if "worker" in body:
        if not project_id:
            raise ValueError("The default worker harness is chosen per project")
        choice["worker"] = get(db, "worker", body["worker"]).id
    if "autonomy" in body:
        autonomy = body["autonomy"]
        if autonomy in (None, ""):
            choice.pop("autonomy", None)
        elif autonomy not in AUTONOMY:
            raise ValueError("Unknown autonomy")
        elif autonomy not in AVAILABLE_AUTONOMY:
            raise ValueError(f"Autonomy '{AUTONOMY[autonomy][0]}' is not yet available")
        else:
            choice["autonomy"] = autonomy
    db.set_setting(SELECTION + (project_id or "global"), choice)
    harness, autonomy = manager_turn(db, project_id)
    if (harness, autonomy) == before:
        return None
    return (f"Manager harness: {harness.name} (autonomy: {AUTONOMY[autonomy][0]}). "
            "It applies from the next manager turn.")


def _usable(db, project_id):
    try:
        manager_turn(db, project_id)
        return True
    except ValueError:
        return False


def record_task(db, conn, project_id, task_id, harness):
    """Set once on the task row (cards list it without touching cold event payloads), plus an audit event."""
    choice = {"id": harness.id, "name": harness.name}
    conn.execute("UPDATE tasks SET harness=? WHERE id=? AND harness IS NULL", (json.dumps(choice), task_id))
    db.event(conn, TASK_EVENT, choice, project_id, task_id)


def task_choice(db, task_id):
    row = db.one("SELECT harness FROM tasks WHERE id=?", (task_id,))
    return json.loads(row["harness"]) if row and row["harness"] else None


def task_harness(db, task):
    """The worker harness a task runs under: the one recorded when it was created (a task made before harnesses
    existed records its project's default at its first dispatch). It never changes afterwards."""
    choice = task_choice(db, task["id"])
    if not choice:
        harness = get(db, "worker", default_worker(db, task["project_id"]))
        with db.transaction() as conn:
            record_task(db, conn, task["project_id"], task["id"], harness)
        return harness
    return get(db, "worker", choice["id"])


def notice(db, conversation_id, text):
    """A short application notice in the chat (the next manager turn sees it in its history too)."""
    with db.transaction() as conn:
        conn.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,?)",
                     (new_id(), conversation_id, "assistant", text, "completed", None, time.time()))
