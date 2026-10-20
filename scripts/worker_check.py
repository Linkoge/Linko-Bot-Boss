"""Informational worker self-check; no database, queue changes, commits or merges."""
import argparse
import json
import sys
from pathlib import Path

# Invoked by absolute path using the manager's interpreter from any project's cwd.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_manager.core import run_gate, run_secret_gate  # noqa: E402
from ai_manager.fence import main_branch  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="Project worktree")
    parser.add_argument("--base", required=True, help="Assignment base commit")
    args = parser.parse_args()
    root = args.root.resolve()
    result = run_gate(root, args.base)
    secrets = run_secret_gate(root, main_branch(root))
    # Keep the gate's diagnostics unchanged, adding the delivery scanner's safe findings.
    result = {**result, "ok": result["ok"] and secrets["ok"],
              "checks": result["checks"] + secrets["checks"],
              "failures": (result["failures"] + secrets["failures"])[:30],
              "count": result["count"] + secrets["count"]}
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
