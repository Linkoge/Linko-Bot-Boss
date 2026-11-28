"""The checkout paths a running instance needs, and whether another instance's code view has them.

An instance that runs as another user (README "Multiple instances", e.g. kaxa) does not see this checkout directly: a
root-owned host adapter, ``/usr/local/libexec/linko-<name>-instance``, bind-mounts a fixed list of checkout paths
(its ``mounts`` list) read-only into that instance's code directory. A top-level path the application reads at runtime
but missing from that list makes the instance fail at its restart, and a deploy then rolls back. RUNTIME_PATHS is the
one list of what the application needs; tests/test_code_view.py starts the app from a copy holding only these paths,
and the deploy (scripts/deploy.py) and doctor compare every instance's adapter list with it before a restart.

The permanent layout (``scripts/instance_adapter.py``, adapter template ``deploy/instance-adapter.py``) is
"whole-checkout": the adapter bind-mounts the WHOLE checkout read-only (MASKED entries hidden), and the instance user
reads it through the shared group GROUP, whose default ACL ``g:GROUP:rX`` new files inherit. ``inspect`` is the one
check of an instance's code view: a whole-checkout adapter must mount this checkout, its user must be in GROUP and
every tracked file must be readable by GROUP; a per-path (legacy) adapter must still list every RUNTIME_PATHS entry.
Standard library only: scripts/deploy.py imports this module.
"""

import ast
import grp
import os
import pwd
import re
import stat
import struct
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import fsmode

# Top-level checkout paths the application reads at runtime (imports, prompts, built-in harnesses). A new one must be
# added here; a whole-checkout adapter sees it, an old per-path adapter needs it in its mounts list until it is
# migrated (scripts/instance_adapter.py apply NAME; docs/INSTALL.md "Instances as other users").
RUNTIME_PATHS = ("ai_manager", "harnesses", "README.md", "RULES.md")
ADAPTER = "/usr/local/libexec/linko-{name}-instance"
WHOLE = "whole-checkout"  # the adapter LAYOUT value of the whole-checkout mount
GROUP = "linko-code"  # instance users read the checkout through this group
# Top-level checkout entries an instance never sees (the adapter masks them); any .env-style name too. The adapter
# template keeps its own copy (it runs as root and imports nothing from the checkout); tests keep both equal.
MASKED = (".git", ".env", ".data", "secrets", ".linko-runtime")
MIGRATE = "sudo python3 {repo}/scripts/instance_adapter.py apply {name}"
DEFAULT_ACL = "system.posix_acl_default"


def adapter_mounts(path):
    """The (source, target) pairs of an adapter's ``mounts = [...]`` list, or None when it cannot be read."""
    try:
        text = Path(path).read_text()
    except (OSError, UnicodeDecodeError):
        return None
    match = re.search(r"^mounts\s*=\s*(\[.*?\])\s*$", text, re.M | re.S)
    try:
        value = ast.literal_eval(match.group(1)) if match else None
    except (ValueError, SyntaxError):
        return None
    if not isinstance(value, list) or not all(isinstance(m, (list, tuple)) and len(m) == 2 for m in value):
        return None
    return [(str(source), str(target)) for source, target in value]


def missing(repo, mounts):
    """RUNTIME_PATHS present in this checkout that the instance's mounts do not bind (or a parent of them)."""
    sources = [Path(source) for source, _ in mounts]
    gaps = []
    for name in RUNTIME_PATHS:
        path = Path(repo) / name
        if path.exists() and not any(path == source or source in path.parents for source in sources):
            gaps.append(name)
    return gaps


def check(repo, name, adapter=None):
    """(gaps, adapter path, readable) for one instance: gaps is [] when its code view has every runtime path."""
    adapter = str(adapter or ADAPTER.format(name=name))
    if not Path(adapter).exists():
        return [], adapter, False  # no adapter: the instance reads the checkout itself (same user or full bind)
    mounts = adapter_mounts(adapter)
    if mounts is None:
        return [], adapter, False
    return missing(repo, mounts), adapter, True


def fix(name, gaps, adapter, repo=None):
    migrate = f"; or migrate it to the whole-checkout mount: {MIGRATE.format(repo=repo, name=name)}" if repo else ""
    return (f"add {', '.join(gaps)} to the mounts list in {adapter} (root host adapter for instance {name}); "
            "until then that instance cannot start this commit" + migrate)


def masked(names):
    """The entries of ``names`` (top-level checkout names) that an instance never sees."""
    return sorted(n for n in names if n in MASKED or n.startswith(".env") or n.endswith(".env"))


def adapter_settings(path):
    """Top-level ``NAME = literal`` assignments of an adapter (never executed), or None when it cannot be parsed."""
    try:
        tree = ast.parse(Path(path).read_text())
    except (OSError, UnicodeDecodeError, SyntaxError, ValueError):
        return None
    settings = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                settings[node.targets[0].id] = ast.literal_eval(node.value)
            except (ValueError, SyntaxError, TypeError):
                continue
    return settings


def _perm(path, info):
    """Read (and, for a directory, search) bits a member of the group needs: ``rX`` like setfacl."""
    return 5 if stat.S_ISDIR(info.st_mode) else 4


def group_can(path, gid, need=None):
    """Whether a member of group ``gid`` (not the owner, no named user entry) has ``need`` on ``path`` (POSIX ACL
    access check from the access ACL or, without one, the mode bits)."""
    info = os.lstat(path)
    need = _perm(path, info) if need is None else need
    entries = fsmode.acl_entries(path)
    if entries is None:
        entries = [(fsmode.GROUP_OBJ, (info.st_mode >> 3) & 7, 0xFFFFFFFF), (fsmode.OTHER, info.st_mode & 7, 0xFFFFFFFF)]
    mask = next((perm for tag, perm, _ in entries if tag == fsmode.MASK), 7)
    matched = [perm & mask for tag, perm, ident in entries
               if (tag == fsmode.GROUP_OBJ and info.st_gid == gid) or (tag == fsmode.GROUP and ident == gid)]
    if matched:
        return any(perm & need == need for perm in matched)
    return next((perm for tag, perm, _ in entries if tag == fsmode.OTHER), 0) & need == need


def has_default(path, gid):
    """Whether directory ``path`` hands ``g:gid:r-x`` (or more) to new entries through its default ACL."""
    try:
        raw = os.getxattr(path, DEFAULT_ACL)
    except OSError:
        return False
    return any(tag == fsmode.GROUP and ident == gid and perm & 5 == 5
               for tag, perm, ident in (fsmode.ENTRY.unpack_from(raw, o) for o in range(4, len(raw), fsmode.ENTRY.size)))


def grant(path, gid):
    """Add ``g:gid:rX`` to the access ACL of a file the caller owns and recalculate the mask, like ``setfacl -m``.
    Owner and other bits stay as they are."""
    info = os.lstat(path)
    perm = 5 if stat.S_ISDIR(info.st_mode) or info.st_mode & 0o111 else 4
    entries = fsmode.acl_entries(path) or [(fsmode.USER_OBJ, (info.st_mode >> 6) & 7, 0xFFFFFFFF),
                                           (fsmode.GROUP_OBJ, (info.st_mode >> 3) & 7, 0xFFFFFFFF),
                                           (fsmode.OTHER, info.st_mode & 7, 0xFFFFFFFF)]
    entries = [e for e in entries if not (e[0] == fsmode.GROUP and e[2] == gid) and e[0] != fsmode.MASK]
    entries.append((fsmode.GROUP, perm, gid))
    mask = 0
    for tag, value, _ in entries:
        if tag in (fsmode.USER, fsmode.GROUP_OBJ, fsmode.GROUP):
            mask |= value
    entries.append((fsmode.MASK, mask, 0xFFFFFFFF))
    entries.sort(key=lambda e: (e[0], e[2]))
    os.setxattr(path, fsmode.ACCESS, struct.pack("<I", fsmode.VERSION) + b"".join(fsmode.ENTRY.pack(*e) for e in entries))


def tracked(repo):
    """Every tracked path of the checkout plus each directory above one (the root included), skipping MASKED."""
    result = subprocess.run(["git", "-C", str(repo), "-c", "core.fsmonitor=false", "ls-files", "-z"],
                            capture_output=True, timeout=60, env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"})
    if result.returncode:
        raise OSError("git ls-files failed: " + result.stderr.decode(errors="replace").strip()[:200])
    names = {"."}
    for name in result.stdout.decode(errors="surrogateescape").split("\0"):
        if not name or masked([name.split("/", 1)[0]]):
            continue
        parts = name.split("/")
        names.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))
    return sorted(names)


def unreadable(repo, gid, repair=False):
    """Tracked paths a member of ``gid`` cannot read (directories: read and search). With ``repair``, first grant
    ``g:gid:rX`` on each one the caller owns, e.g. a file a tool rewrote with mode 600 (its mask dropped to ---)."""
    missing = []
    for name in tracked(repo):
        path = Path(repo) / name
        try:
            if os.path.islink(path) or group_can(path, gid):
                continue
            if repair and os.lstat(path).st_uid == os.geteuid():
                grant(path, gid)
                if group_can(path, gid):
                    continue
        except FileNotFoundError:
            continue  # deleted but not yet staged: nothing to read
        except OSError:
            pass
        missing.append(name)
    return missing


@dataclass
class Report:
    """What ``inspect`` found for one instance: ``layout`` is None (no adapter), "legacy" or WHOLE."""
    adapter: str
    layout: str = None
    problems: list = field(default_factory=list)  # each stops a deploy
    notes: list = field(default_factory=list)  # logged / warned, never fatal


def member(user, group):
    try:
        entry = pwd.getpwnam(user)
    except KeyError:
        return False
    return entry.pw_gid == group.gr_gid or user in group.gr_mem


def inspect(repo, name, adapter=None, repair=False):
    """The one code-view check of an instance (deploy before a restart, doctor, ``instance_adapter.py check``)."""
    adapter = str(adapter or ADAPTER.format(name=name))
    report = Report(adapter)
    if not Path(adapter).exists():
        return report  # no adapter: the instance reads the checkout itself (same user)
    settings = adapter_settings(adapter)
    if not settings or settings.get("LAYOUT") != WHOLE:
        report.layout = "legacy"
        gaps, _, readable = check(repo, name, adapter)
        if gaps:
            report.problems.append(f"cannot see {', '.join(gaps)}: " + fix(name, gaps, adapter, repo))
        elif not readable:
            report.notes.append(f"cannot read the mounts list in {adapter}; code view unchecked")
        else:
            report.notes.append(f"{adapter} still binds a per-path list (RUNTIME_PATHS checked); migrate it once to "
                                f"the whole-checkout mount: {MIGRATE.format(repo=repo, name=name)}")
        return report
    report.layout = WHOLE
    apply = MIGRATE.format(repo=repo, name=name)
    checkout = str(settings.get("CHECKOUT", ""))
    if not checkout or Path(checkout).resolve() != Path(repo).resolve():
        report.problems.append(f"{adapter} mounts {checkout or 'no checkout'}, not {repo}; re-run: {apply}")
        return report
    group_name, user = str(settings.get("GROUP") or GROUP), str(settings.get("USER") or name)
    try:
        group = grp.getgrnam(group_name)
    except KeyError:
        report.problems.append(f"group {group_name} does not exist; re-run: {apply}")
        return report
    if not member(user, group):
        report.problems.append(f"user {user} is not in group {group_name}; re-run: {apply}")
    if not has_default(repo, group.gr_gid):
        report.notes.append(f"{repo} has no default ACL g:{group_name}:rX, so new top-level entries are not shared; "
                            f"re-run: {apply}")
    try:
        missing = unreadable(repo, group.gr_gid, repair=repair)
    except OSError as error:
        report.problems.append(f"cannot list the checkout: {error}")
        return report
    if missing:
        shown = ", ".join(missing[:5]) + (f" and {len(missing) - 5} more" if len(missing) > 5 else "")
        report.problems.append(f"group {group_name} cannot read {shown}; as their owner run "
                               f"setfacl -m g:{group_name}:rX on them, or re-run: {apply}")
    return report
