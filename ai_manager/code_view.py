"""The checkout paths a running instance needs, and whether another instance's code view has them.

An instance that runs as another user (README "Multiple instances", e.g. kaxa) does not see this checkout directly: a
root-owned host adapter, ``/usr/local/libexec/linko-<name>-instance``, bind-mounts a fixed list of checkout paths
(its ``mounts`` list) read-only into that instance's code directory. A top-level path the application reads at runtime
but missing from that list makes the instance fail at its restart, and a deploy then rolls back. RUNTIME_PATHS is the
one list of what the application needs; tests/test_code_view.py starts the app from a copy holding only these paths,
and the deploy (scripts/deploy.py) and doctor compare every instance's adapter list with it before a restart.
Standard library only: scripts/deploy.py imports this module.
"""

import ast
import re
from pathlib import Path

# Top-level checkout paths the application reads at runtime (imports, prompts, built-in harnesses). A new one must be
# added here AND to every instance adapter's mounts list (docs/INSTALL.md "Instances as other users").
RUNTIME_PATHS = ("ai_manager", "harnesses", "README.md", "RULES.md")
ADAPTER = "/usr/local/libexec/linko-{name}-instance"


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


def fix(name, gaps, adapter):
    return (f"add {', '.join(gaps)} to the mounts list in {adapter} (root host adapter for instance {name}); "
            "until then that instance cannot start this commit")
