"""Bounded automatic follow-ups: what a manager turn started by a worker result notice may do. See README.md.

A result notice is application transport, never owner authorization. The one exception is finishing or fixing the
owner's ORIGINAL request with `delegate` or `continue` (a normal queued task), at most LIMIT times per owner-typed
request (the chain root), never twice for the same failure cause in a row, never for paused or offline projects.
Every other action is rejected here, by the application, whatever the manager model says.
"""

import hashlib
import json
import re
import time

from .core import REPAIR_ROUNDS

LIMIT = 3
TRIGGERS = ("repair_exhausted", "incomplete", "deploy_failed")
# Read-only actions are harmless in a notice turn; save_todo is allowed only as an 'idea' (see authorize).
NOTICE_READS = {"deployment_status", "repair_status", "list_todos", "read_document", "list_files", "search_code",
                "screenshot", "read_memory", "read_project", "list_memory", "list_history", "read_task", "read_result",
                "list_tasks", "read_history"}
REFUSED = "; a worker result notice is not explicit owner authorization, so the owner decides what happens next"
KIND = "task.auto_followup"


def refuse(why):
    return ValueError("Automatic follow-up refused: " + why + REFUSED)


def cause_key(trigger, text):
    """Stable identity of a failure cause: ids, hashes, numbers and spacing do not make a failure 'different'."""
    text = re.sub(r"\b[0-9a-f]{7,}\b", "#", (text or "").lower())
    text = re.sub(r"\d+", "#", text)
    return trigger + ":" + re.sub(r"\s+", " ", text).strip()[:240]


class Followups:
    def __init__(self, db, registry, pipeline):
        self.db, self.registry, self.pipeline = db, registry, pipeline

    def source(self, execution_id):
        """The worker result a notice turn reports (the application's own delivery link), or None."""
        return self.db.one(
            """SELECT e.id AS execution_id,e.task_id,t.project_id,t.state,t.rounds,t.blocker,t.chain_root
            FROM executions e JOIN tasks t ON t.id=e.task_id WHERE e.delivery_execution=?""", (execution_id,))

    def used(self, root):
        rows = self.db.all("SELECT payload FROM events WHERE kind=? AND json_extract(history_text(payload),'$.root')=? "
                           "ORDER BY seq", (KIND, root))
        return [json.loads(r["payload"]) for r in rows]

    def authorize(self, action, args, selected_project, params):
        """Gate for a manager action triggered by a result notice. Returns None, or the follow-up plan for
        delegate/continue (the limit and the loop guard are applied by reserve(), inside the task's own transaction)."""
        if action == "save_todo":
            if args.get("kind") != "idea":
                raise refuse("a notice may only record suggestions as To-Dos of kind 'idea'")
            if args.get("todo_id"):
                entry = self.db.one("SELECT kind FROM todos WHERE id=?", (args["todo_id"],))
                if not entry or entry["kind"] != "idea":
                    raise refuse("a notice may not change a To-Do that is not an idea")
            return None
        if action in NOTICE_READS:
            return None
        if action not in {"delegate", "continue"}:
            raise refuse(f"{action} always needs an owner-typed message")
        if args.get("todos"):
            raise refuse("starting To-Dos needs an owner-typed message")
        source = self.source(params.get("manager_execution_id"))
        if not source:
            raise refuse("there is no worker result to follow up")
        if not source["chain_root"]:
            raise refuse("that task did not come from an owner-typed request")
        project_id = args.get("project_id") or selected_project
        if project_id != source["project_id"]:
            raise refuse("a follow-up stays in the project of the result")
        project = self.registry.get(project_id)
        if project["status"] != "active":
            raise refuse(f"the project is {project['status']}")
        if project.get("github_tier") == "offline":
            raise refuse("the project is offline")
        if source["state"] not in {"done", "failed"}:
            raise refuse("the result is no longer current")
        if action == "continue":
            task = self.db.one("SELECT project_id,chain_root FROM tasks WHERE id=?", (args.get("task_id"),))
            if not task or task["project_id"] != project_id or task["chain_root"] != source["chain_root"]:
                raise refuse("continue is only for a task of the same request")
        elif args.get("task_id"):
            raise refuse("use continue to add instructions to an existing task")
        trigger = args.get("followup_trigger")
        if trigger not in TRIGGERS:
            raise refuse("followup_trigger must be one of " + ", ".join(TRIGGERS))
        if trigger == "repair_exhausted":
            if source["state"] != "failed" or source["rounds"] < REPAIR_ROUNDS or "Cancelled" in source["blocker"]:
                raise refuse("the task did not fail after using all its repair rounds")
            text = source["blocker"]
        elif trigger == "deploy_failed":
            last = (self.pipeline.status(project_id) or {}).get("last_result") or {}
            if (last.get("task_id") != source["task_id"] or last.get("status") not in {"failed", "rolled_back"}
                    or not last.get("repairable")):
                raise refuse("no failed or rolled-back deploy for a code reason is recorded for this task")
            text = last.get("reason", "")
        else:
            text = args.get("reason", "").strip()
            if source["state"] != "done" or not text:
                raise refuse("incomplete needs a finished task and a reason naming what is not done")
        return {"root": source["chain_root"], "parent": source["task_id"], "trigger": trigger,
                "cause": cause_key(trigger, text), "action": action}

    def reserve(self, conn, plan, task_id, params, summary):
        """Count this follow-up against its chain (limit, loop guard), record it and tell the originating chat.
        Runs inside the caller's transaction, so a refusal leaves no task behind. Returns the follow-up number."""
        previous = self.used(plan["root"])
        if len(previous) >= LIMIT:
            raise refuse(f"the limit of {LIMIT} automatic follow-ups for this request is reached; tell the owner what is still open")
        if previous and previous[-1]["cause"] == plan["cause"]:
            raise refuse("it would repeat the failure cause of the previous follow-up; tell the owner what is still open")
        number = len(previous) + 1
        task = conn.execute("SELECT project_id FROM tasks WHERE id=?", (task_id,)).fetchone()
        self.db.event(conn, KIND, {"root": plan["root"], "number": number, "cause": plan["cause"], "trigger": plan["trigger"],
                                   "action": plan["action"], "source_task": plan["parent"]},
                      task["project_id"], task_id, params.get("manager_execution_id"))
        line = " ".join((summary or "").split())[:120] or "continuing the original request"
        conversation = params.get("conversation_id")
        if conversation and conn.execute("SELECT 1 FROM conversations WHERE id=?", (conversation,)).fetchone():
            key = hashlib.sha256(f"{plan['root']}:{number}".encode()).hexdigest()[:24]
            conn.execute("INSERT OR IGNORE INTO messages(id,conversation_id,role,content,status,created_at) "
                         "VALUES(?,?,'assistant',?,'completed',?)",
                         ("followup-" + key, conversation, f"Auto follow-up {number}/{LIMIT}: {line}", time.time()))
        return number
