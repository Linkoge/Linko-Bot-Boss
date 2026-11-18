"""The stale browser scratch sweep and the check runner's process-group kill, with temp dirs only."""
import os
import time

import pytest

from ai_manager.core import execute
from ai_manager.tempsweep import sweep_stale


def aged(path, seconds):
    old = time.time() - seconds
    os.utime(path, (old, old))


def test_sweep_removes_only_old_owned_prefixed_folders(tmp_path):
    stale, fresh, other, plain = (tmp_path / n for n in ('linko-browser-old', 'linko-browser-new', 'keep-old', 'linko-browser-file'))
    for folder in (stale, fresh, other):
        (folder / 'Default').mkdir(parents=True)
        (folder / 'Default' / 'data').write_text('x' * 10)
    plain.write_text('not a folder')
    target = tmp_path / 'linko-browser-link'
    target.symlink_to(other)
    for path in (stale, other, plain):
        aged(path, 7200)
    os.utime(target, (time.time() - 7200,) * 2, follow_symlinks=False)
    assert sweep_stale(directory=tmp_path) == ['linko-browser-old']
    assert not stale.exists() and fresh.exists() and other.exists() and plain.exists() and target.is_symlink()


def test_sweep_ignores_a_missing_directory(tmp_path):
    assert sweep_stale(directory=tmp_path / 'absent') == []


def test_timeout_kills_the_whole_process_group(tmp_path):
    marker = tmp_path / 'child.pid'
    script = f"import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); open({str(marker)!r},'w').write(str(p.pid)); time.sleep(60)"
    code, _, timed_out = execute([os.sys.executable, '-c', script], tmp_path, os.environ, 2)
    assert timed_out
    pid = int(marker.read_text())
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        pytest.fail('grandchild survived the timeout')
