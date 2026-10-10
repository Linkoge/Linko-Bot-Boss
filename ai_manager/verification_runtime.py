"""Project-scoped, owner-provisioned offline Python runtime; no host bind mounts."""

import hashlib
import json
import shutil
from pathlib import Path

from .policies import Policies


def tree_identity(root):
    hashes = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            target = path.resolve()
            if not target.is_relative_to(root.resolve()):
                raise ValueError("Runtime artifact symlink escapes its package")
        elif path.is_file():
            hashes[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()


class VerificationRuntime:
    def __init__(self, db, settings):
        self.db, self.settings = db, settings

    async def provision(self, project_id, workspace, task_id, execution_id):
        record = self.db.setting("verification_runtime:" + project_id)
        if not record:
            return
        source = Path(record["path"])
        boundary = self.settings.data_dir / "verification-runtimes" / project_id
        if source.is_symlink() or not source.resolve().is_relative_to(boundary.resolve()):
            raise ValueError("Runtime artifact outside assigned project boundary")
        if tree_identity(source) != record["sha256"]:
            raise ValueError("Approved offline runtime artifact changed; owner reprovisioning is required")
        definition = workspace.root / ".linko-runtime.json"
        if (
            not definition.is_file()
            or definition.is_symlink()
            or json.loads(definition.read_text()).get("sha256") != record["sha256"]
        ):
            raise ValueError("Worker configuration does not identify the approved project runtime")
        await Policies(self.db).authorize(
            project_id,
            task_id,
            execution_id,
            "install_dependencies",
            {"runtime_sha256": record["sha256"]},
            "offline-runtime",
            "Copy approved offline Python and dependencies into this isolated project workspace",
            resume_status=self.db.one("SELECT status FROM tasks WHERE id=?", (task_id,))["status"]
            if task_id
            else "running",
        )
        target = workspace.root / ".linko-runtime"
        if target.exists() or target.is_symlink():
            if target.is_symlink() or tree_identity(target) != record["sha256"]:
                raise ValueError("Workspace runtime differs from approved artifact; do not overwrite it")
        else:
            shutil.copytree(source, target, symlinks=True)
        with self.db.transaction() as conn:
            self.db.event(
                conn,
                "verification.runtime_provisioned",
                {
                    "sha256": record["sha256"],
                    "python": record["python"],
                    "offline": True,
                    "network": False,
                    "host_home": False,
                },
                project_id,
                task_id,
                execution_id,
            )
