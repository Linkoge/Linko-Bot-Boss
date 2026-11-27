"""File delivery: the end of a task whose worker harness sets ``"delivery": "files"`` (Office, Humanizer). README.md.

The task runs in its own worktree like any other, but its result is a set of documents, not code: the application
never runs the code checks, never merges into main and never requests a deploy for it. Instead, after the worker's turn:

1. check(): the file checks, which fail like a check and go back to the worker as a repair round: no original (a file
   that existed at the task's base) changed or deleted, outputs are regular files (no symlinks out of the workspace),
   at most MAX_FILES of at most MAX_FILE_BYTES each, and no key-shaped literal in any output (the same patterns as the
   commit secret scan; .docx outputs are scanned as their text).
2. deliver(): every new output file is stored as a chat attachment (downloadable from the result reply), and, when it
   sits next to a project document it was made from ("<name> summary.md" beside "<name>.pdf"), copied next to that
   document in the project folder, never overwriting: a taken name gets " 2", " 3", ...

Outputs are the files the task added relative to its base, committed or not; the git-ignored work folder
(`.linko-work/`) never counts. Delivery is idempotent: a retried run reuses the stored attachment and the identical
copy instead of making new ones.
"""
import hashlib
import mimetypes
import os
import subprocess
import time
import zipfile
from pathlib import Path

from .attachments import MAX_STORAGE, extract_docx
from .push_diagnosis import precheck

MAX_FILES = 20
MAX_FILE_BYTES = 50 * 1024 * 1024
SCAN_BYTES = 5_000_000
CHECK = "files"


def _git(root, *args):
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, timeout=60,
                            env={**os.environ, "LC_ALL": "C"})
    if result.returncode:
        raise ValueError("git " + args[0] + " failed: " + result.stderr.decode("utf-8", "replace").strip()[:300])
    return result.stdout.decode("utf-8", "surrogateescape")


def outputs(root, base):
    """(new, changed): paths this task added since base (committed, staged or untracked, not ignored) and the paths of
    originals it modified or deleted."""
    fields = _git(root, "diff", "--no-renames", "--name-status", "-z", base).split("\0")
    new, changed = set(), set()
    for status, path in zip(fields[::2], fields[1::2]):
        (new if status == "A" else changed).add(path)
    new |= {p for p in _git(root, "ls-files", "--others", "--exclude-standard", "-z").split("\0") if p}
    work = lambda p: p == ".linko-work" or p.startswith(".linko-work/")  # noqa: E731 - the ignored work folder
    return sorted(p for p in new if not work(p)), sorted(p for p in changed if not work(p))


def _text(path):
    if path.suffix.lower() == ".docx":
        try:
            return extract_docx(path)
        except (ValueError, OSError, zipfile.BadZipFile, KeyError):
            pass  # not a readable document: scan its bytes as they are
    return path.read_bytes()[:SCAN_BYTES].decode("utf-8", "replace")


def secrets(root, paths):
    """Key-shaped literals in the output files, as precheck() hits (path and line, never the value)."""
    parts = []
    for rel in paths:
        lines = _text(Path(root) / rel).splitlines()
        parts.append(f"+++ b/{rel}\n@@ -0,0 +1,{len(lines)} @@\n" + "".join("+" + line + "\n" for line in lines))
    return precheck("".join(parts).encode("utf-8", "replace")[:SCAN_BYTES * 2])


def check(root, base):
    """The file checks of a files-delivery task, in the gate's result shape ({ok, checks, failures, count, output})."""
    root = Path(root)
    new, changed = outputs(root, base)
    failures = [f"files: {p} is an original and was changed or deleted; restore it (git checkout {base[:12]} -- "
                f"'{p}') and write the result to a new file" for p in changed]
    regular = []
    for rel in new:
        path = root / rel
        if path.is_symlink() or not path.is_file():
            failures.append(f"files: {rel} is not a regular file in the workspace; write outputs as plain new files")
        elif path.stat().st_size > MAX_FILE_BYTES:
            failures.append(f"files: {rel} is larger than {MAX_FILE_BYTES // (1024 * 1024)} MiB")
        else:
            regular.append(rel)
    if len(new) > MAX_FILES:
        failures.append(f"files: {len(new)} new files; deliver at most {MAX_FILES} and keep scratch files in the "
                        "git-ignored work folder .linko-work/")
    failures += [f"secrets: {h['path']}:{h['line']} looks like a {h['type']}; remove it from the output (rule 10)"
                 for h in secrets(root, regular)]
    return {"ok": not failures, "checks": sorted({f.split(":", 1)[0] for f in failures}), "failures": failures[:30],
            "count": len(failures), "output": "\n".join(failures)[-3000:]}


def attach(db, data, name, task, rel):
    """Store one output as a chat attachment (same store and record as an owner upload); returns its id."""
    digest = hashlib.sha256(data).hexdigest()
    identifier = hashlib.sha256(f"{task['id']}\0{rel}\0{digest}".encode()).hexdigest()[:32]
    store = Path(db.path).parent / "chat-workspace" / "attachments"
    store.mkdir(parents=True, exist_ok=True, mode=0o700)
    if (item := db.setting("attachment:" + identifier)) and (store / item["stored_name"]).is_file():
        return identifier  # a retried delivery
    suffix = Path(name).suffix.lower()
    stored = identifier + (suffix if suffix[1:].isalnum() and len(suffix) <= 17 else ".bin")
    if sum(p.stat().st_size for p in store.iterdir() if p.is_file()) + len(data) > MAX_STORAGE:
        raise ValueError("attachment storage is full")
    path = store / stored
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        path.unlink(missing_ok=True)
        with path.open("xb") as file:
            path.chmod(0o600)
            file.write(data)
    db.set_setting("attachment:" + identifier, {
        "id": identifier, "stored_name": stored, "name": name,
        "mime": mimetypes.guess_type(name)[0] or "application/octet-stream", "native_image": False,
        "size": len(data), "sha256": digest, "project_id": task["project_id"],
        "conversation_id": task.get("origin_conversation"), "created_at": time.time(),
        "source": "delivery", "task_id": task["id"]})
    return identifier


def source_beside(root, base, rel):
    """True when the output sits next to a project document it was named after ("Book summary.md" by "Book.pdf")."""
    parent = str(Path(rel).parent)
    listing = _git(root, "ls-tree", "-z", "--name-only", base, *(["--", parent + "/"] if parent != "." else []))
    name = Path(rel).name
    return any(Path(other).name != name and name.startswith(Path(other).stem + " ")
               for other in listing.split("\0") if other)


def copy_beside(shared, rel, data):
    """Copy an output into the project folder at the same place; never overwrites. Returns the copy's relative path."""
    shared = Path(shared).resolve()
    folder = shared / Path(rel).parent
    if folder.is_symlink() or not folder.is_dir() or shared not in (folder.resolve(), *folder.resolve().parents):
        return None
    original, number = Path(rel).name, 2
    target = folder / original
    while True:
        if target.is_file() and not target.is_symlink() and target.read_bytes() == data:
            return str(target.relative_to(shared))  # the same file is already there: a retried delivery
        if not os.path.lexists(target):
            try:
                with target.open("xb") as file:
                    file.write(data)
                return str(target.relative_to(shared))
            except FileExistsError:
                pass
        target = folder / f"{Path(original).stem} {number}{Path(original).suffix}"
        number += 1


def deliver(db, registry, task, project, root):
    """Attach and copy every output; returns the task's result record (stored where a merge receipt would be)."""
    root, base = Path(root), task["workspace_base"]
    shared = Path(registry.allowed_path(project["path"]))
    files = []
    for rel in outputs(root, base)[0]:
        data = (root / rel).read_bytes()
        entry = {"name": Path(rel).name, "path": rel, "size": len(data), "id": None, "copy": None}
        try:
            entry["id"] = attach(db, data, entry["name"], task, rel)
        except (OSError, ValueError) as error:
            entry["error"] = "not attached: " + str(error)[:200]
        if shared.resolve() != root.resolve() and source_beside(root, base, rel):
            try:
                entry["copy"] = copy_beside(shared, rel, data)
            except OSError as error:
                entry["copy_error"] = "not copied: " + str(error)[:200]
        files.append(entry)
    count, copied = len(files), sum(1 for f in files if f["copy"])
    summary = f"Delivered {count} file{'' if count == 1 else 's'}"
    if copied:
        summary += f"; {copied} copied next to the original in the project folder"
    if not count:
        summary += ": the worker left no new output files"
    return {"state": "delivered", "count": count, "files": files, "summary": summary,
            "deploy": False, "deployment": "files delivered; nothing merged or deployed"}


def references(merge):
    """The chat attachment references of a delivered result (for the reply that reports it)."""
    if not isinstance(merge, dict) or merge.get("state") != "delivered":
        return []
    return [{"id": f["id"], "name": f["name"], "size": f["size"],
             "mime": mimetypes.guess_type(f["name"])[0] or "application/octet-stream",
             "handling": "Delivered output file" + (f"; also saved as {f['copy']} in the project" if f.get("copy") else "")}
            for f in merge.get("files", []) if f.get("id")]
