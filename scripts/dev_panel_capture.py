#!/usr/bin/env python3
"""Dev Panel's LINKO_PANEL_NODE entry point: same stdin/stdout contract, shared engine.

Dev Panel passes its old capture.mjs as argv[1]; deliberately do not execute it.
Its screenshot session management, duplicate filtering and download UI stay intact.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / '.venv' / 'bin' / 'python'
if PYTHON.exists() and Path(sys.prefix).resolve() != (ROOT / '.venv').resolve():
    os.execv(str(PYTHON), [str(PYTHON), __file__, *sys.argv[1:]])
sys.path.insert(0, str(ROOT))
from ai_manager.screenshots import capture  # noqa: E402

if __name__ == '__main__':
    try:
        config = json.load(sys.stdin)
        config['legacy'] = True
        print(json.dumps(asyncio.run(capture(config))))
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
