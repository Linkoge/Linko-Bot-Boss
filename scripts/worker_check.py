"""Informational worker self-check; no database, queue changes, commits or merges."""
import argparse
import json
import re
import sys
from pathlib import Path

# Invoked by absolute path using the manager's interpreter from any project's cwd.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_manager.core import run_gate  # noqa: E402


def stale_pitfalls(root):
    """Advisory only: find entries referring to missing backticked repo-relative paths."""
    try:
        source = (root / "AGENTS.md").read_text()
    except (OSError, UnicodeError):
        return []
    section = re.search(r"(?ms)^## Known pitfalls[ \t]*\r?\n(.*?)(?=^#{1,2} |\Z)", source)
    if not section:
        return []
    findings = []
    for entry in re.finditer(r"(?ms)^### [^\n]+\n.*?(?=^### |\Z)", section[1]):
        missing = []
        for reference in re.findall(r"`([^`\n]+)`", entry[0]):
            path = re.sub(r":\d+(?::\d+)?$", "", reference.split("#", 1)[0])
            # Recognize paths with a slash or extension, not commands, URLs or function calls.
            if not re.fullmatch(r"(?:[\w.-]+/)*[\w.-]+/?", path) or ".." in Path(path).parts:
                continue
            if "/" not in path and not Path(path).suffix:
                continue
            try:
                if not (root / path).exists() and path not in missing:
                    missing.append(path)
            except OSError:
                continue
        if missing:
            findings.append({"entry": entry[0].strip(), "missing_paths": missing})
    return findings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="Project worktree")
    parser.add_argument("--base", required=True, help="Assignment base commit")
    args = parser.parse_args()
    root = args.root.resolve()
    result = run_gate(root, args.base)
    result["stale_pitfalls"] = stale_pitfalls(root)
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
