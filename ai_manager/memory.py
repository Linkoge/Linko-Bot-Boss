"""Preserve owner text and project structured state into managed Markdown sections."""

import time

from .registry import Registry

START = "<!-- AI_MANAGER_GENERATED_START -->"
END = "<!-- AI_MANAGER_GENERATED_END -->"


def managed(original, generated):
    block = f"{START}\n{generated}\n{END}"
    if START in original and END in original:
        before, remainder = original.split(START, 1)
        _, after = remainder.split(END, 1)
        return before + block + after
    return original.rstrip() + "\n\n" + block + "\n"


def sync_project(db, project_id):
    with db.transaction() as conn:
        project = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if not project:
            return
        tasks = conn.execute(
            "SELECT id,status,priority,description,acceptance,blocker FROM tasks WHERE project_id=? ORDER BY priority DESC,created_at LIMIT 100",
            (project_id,),
        ).fetchall()
        task_lines = ["## Authoritative task queue"]
        for task in tasks:
            task_lines.append(
                f"- [{task['status']}] P{task['priority']} `{task['id'][:8]}` {task['description'][:250]}\n  Acceptance: {task['acceptance'][:250]}"
                + (f"\n  Blocker: {task['blocker'][:250]}" if task["blocker"] else "")
            )
        if not tasks:
            task_lines.append("No tasks recorded.")
        next_task = next((t for t in tasks if t["status"] not in {"completed", "cancelled"}), None)
        handoff = (
            f"## Current handoff\nProject: {project['name']} ({project['status']}); agent {project['agent_id']}.\nReconcile actual Git/files and process identity before resuming. Completion requires independent evidence.\nNext task: "
            + (
                f"{next_task['id']} — {next_task['description'][:500]} ({next_task['status']})"
                if next_task
                else "No outstanding task; ask the owner for the next goal."
            )
        )
        context = f"## Registry context\nObjective: {project['objective'] or 'Not yet specified'}\nSuccess criteria: {project['success_criteria'] or 'Not yet specified'}\nRepository: {project['repository']}\nWorking directory: {project['path']}\nInstructions: {project['instructions']}"
        for name, generated in (("TASKS.md", "\n".join(task_lines)), ("HANDOFF.md", handoff), ("PROJECT.md", context)):
            row = conn.execute(
                "SELECT content FROM memory WHERE project_id=? AND name=?", (project_id, name)
            ).fetchone()
            if row:
                content = managed(row["content"], generated)
                if content != row["content"]:
                    conn.execute(
                        "UPDATE memory SET content=?,revision=revision+1,updated_at=? WHERE project_id=? AND name=?",
                        (content, time.time(), project_id, name),
                    )
        db.event(conn, "memory.reconciled", {}, project_id)
    Registry(db, ()).export_memory(project_id)
