"""`python -m ai_manager.humanize humanize FILE --typos 0.2 --punct 2 [--seed N]`: the Humanizer's level 2 script.

The one implementation is harnesses/humanizer/tools/humanize.py (standard library only, so a worker runs it with any
python3 straight from the harness folder); this module loads it for the application (the Writing settings measure
samples with it) and for the `-m` command line."""
import importlib.util
import sys
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "harnesses" / "humanizer" / "tools" / "humanize.py"


def _load():
    spec = importlib.util.spec_from_file_location("linko_humanize_tool", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tool = _load()
measure, describe, humanize_text, humanize_file = tool.measure, tool.describe, tool.humanize_text, tool.humanize_file
DEFAULT_TYPOS, DEFAULT_PUNCT, MAX_TYPOS, MAX_PUNCT = tool.DEFAULT_TYPOS, tool.DEFAULT_PUNCT, tool.MAX_TYPOS, tool.MAX_PUNCT

if __name__ == "__main__":
    sys.exit(tool.main())
