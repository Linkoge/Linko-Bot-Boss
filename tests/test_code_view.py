"""Instances behind a host adapter's bind list (ai_manager/code_view.py): the runtime path list is complete, the app
starts from exactly those paths, and deploy and doctor stop a commit that an instance's code view cannot run."""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from ai_manager import code_view, doctor

ROOT = Path(__file__).resolve().parents[1]
# Top-level paths ai_manager names on purpose without needing them to start; each is read only by a flow an
# adapter-bound instance does not run at startup. A new name must be added to RUNTIME_PATHS or here, with a reason.
ON_DEMAND = {
    "scripts": "worker self-check command text, onboarding launcher, host repair (main instance installs)",
    "deploy": "Windows enrollment script download (main instance onboarding)",
}
START = '''
import pathlib, sys
import ai_manager
assert pathlib.Path(ai_manager.__file__).resolve().parents[1] == pathlib.Path.cwd().resolve(), ai_manager.__file__
from fastapi.testclient import TestClient
from ai_manager import harness
from ai_manager.codex import AppServer, MANAGER_INSTRUCTIONS
from ai_manager.config import Settings
from ai_manager.core import WORKER_INSTRUCTIONS
from ai_manager.web import create_app

class Idle(AppServer):
    async def start(self):
        raise RuntimeError("no provider in this probe")

settings = Settings()
with TestClient(create_app(settings, Idle(settings), Idle), base_url="http://localhost") as client:
    assert client.get("/api/auth-status").status_code == 200
    assert client.get("/").status_code == 200
assert set(harness.builtins()) == {"linko", "zero", "coding", "zero-worker"} and MANAGER_INSTRUCTIONS and WORKER_INSTRUCTIONS
print("STARTED")
'''


def referenced_top_level():
    """Top-level checkout names ai_manager reads relative to the code root."""
    names = set()
    for path in (ROOT / "ai_manager").glob("*.py"):
        text = path.read_text()
        names |= set(re.findall(r'(?:parents\[1\]|CODE_ROOT)\)?\s*/\s*["\']([^"\'/]+)', text))
    return names


def test_every_top_level_path_ai_manager_reads_is_runtime_or_declared_on_demand():
    referenced = referenced_top_level()
    assert {"RULES.md", "README.md", "harnesses"} <= referenced  # the scan finds the known readers
    assert referenced <= set(code_view.RUNTIME_PATHS) | set(ON_DEMAND), (
        f"{sorted(referenced - set(code_view.RUNTIME_PATHS) - set(ON_DEMAND))}: add to code_view.RUNTIME_PATHS "
        "(and every instance adapter's mounts) or to ON_DEMAND with the reason it is not needed at startup")
    for name in code_view.RUNTIME_PATHS:
        assert (ROOT / name).exists(), name


def code_copy(tmp_path, names):
    copy = tmp_path / "code"
    copy.mkdir()
    for name in names:
        source = ROOT / name
        if source.is_dir():
            shutil.copytree(source, copy / name, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(source, copy / name)
    git = ["git", "-C", str(copy), "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    subprocess.run([*git, "init", "-q"], check=True, timeout=60)
    subprocess.run([*git, "add", "-A"], check=True, timeout=60)
    subprocess.run([*git, "commit", "-qm", "code view"], check=True, timeout=60)
    return copy


def start(tmp_path, copy):
    data, projects = tmp_path / "data", tmp_path / "projects"
    projects.mkdir(exist_ok=True)
    env = {**os.environ, "PYTHONPATH": str(copy), "AI_MANAGER_DATA": str(data),
           "AI_MANAGER_PROJECT_ROOTS": str(projects), "AI_MANAGER_SECURE_COOKIE": "0"}
    env.pop("AI_MANAGER_HARNESS_DIR", None)
    return subprocess.run([sys.executable, "-c", START], cwd=copy, env=env, capture_output=True, text=True, timeout=120)


def test_the_app_starts_from_exactly_the_runtime_paths(tmp_path):
    result = start(tmp_path, code_copy(tmp_path, code_view.RUNTIME_PATHS))
    assert result.returncode == 0 and "STARTED" in result.stdout, result.stderr[-3000:]


def test_a_code_view_without_harnesses_cannot_start(tmp_path):
    """What the kaxa instance hit: the adapter bound ai_manager/ but not the new top-level harnesses/."""
    result = start(tmp_path, code_copy(tmp_path, [n for n in code_view.RUNTIME_PATHS if n != "harnesses"]))
    assert result.returncode != 0 and "harness.json is missing" in result.stderr


def adapter(tmp_path, repo, names, extra=""):
    path = tmp_path / "linko-work-instance"
    mounts = [(str(repo / n), f"/opt/linko-bot-manager/{n}") for n in names]
    mounts.append(("/srv/venv/site-packages", "/opt/linko-runtime/venv/lib/python3.12/site-packages"))
    path.write_text("#!/usr/bin/python3\nimport os\nos.chdir('/')\nmounts = " + repr(mounts) + "\n" + extra
                    + "if True:\n    pass\n")
    return path


def test_adapter_mounts_are_parsed_and_gaps_reported(tmp_path):
    path = adapter(tmp_path, ROOT, ["ai_manager", "README.md", "RULES.md"])
    assert len(code_view.adapter_mounts(path)) == 4
    assert code_view.check(ROOT, "work", path) == (["harnesses"], str(path), True)
    path = adapter(tmp_path, ROOT, code_view.RUNTIME_PATHS, "OPTIONAL = {'x'}\n")
    assert code_view.check(ROOT, "work", path) == ([], str(path), True)
    whole = tmp_path / "whole"
    whole.write_text(f"mounts = [({str(ROOT)!r}, '/opt/linko-bot-manager')]\n")  # a parent bind covers everything
    assert code_view.check(ROOT, "work", whole)[0] == []
    broken = tmp_path / "broken"
    broken.write_text("mounts = [open('/etc/passwd')]\n")  # never evaluated
    assert code_view.adapter_mounts(broken) is None and code_view.check(ROOT, "work", broken) == ([], str(broken), False)
    assert code_view.check(ROOT, "work", tmp_path / "absent") == ([], str(tmp_path / "absent"), False)


@pytest.fixture
def deploy_module():
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import deploy
        yield deploy
    finally:
        sys.path.remove(str(ROOT / "scripts"))


def test_deploy_stops_before_anything_restarts_when_an_instance_cannot_see_a_runtime_path(tmp_path, deploy_module):
    path = adapter(tmp_path, ROOT, ["ai_manager", "README.md", "RULES.md"])
    config = {"repo": str(ROOT), "data": str(tmp_path / "data"),
              "instances": [{"name": "work", "data": str(tmp_path / "work"), "url": "http://127.0.0.1:1",
                             "service": "linko-bot-manager@work.service", "adapter": str(path)}]}
    deploy = deploy_module.Deploy(config)
    with pytest.raises(deploy_module.Stop) as stopped:
        deploy.code_views()
    assert stopped.value.status == "failed" and not stopped.value.repairable
    assert str(stopped.value).startswith("instance work cannot see harnesses: add harnesses to the mounts list in")
    adapter(tmp_path, ROOT, code_view.RUNTIME_PATHS)
    deploy.code_views()  # complete: the deploy goes on


def test_doctor_reports_an_instance_code_view_gap(tmp_path):
    lines = []
    path = adapter(tmp_path, ROOT, ["ai_manager", "README.md", "RULES.md", "harnesses"])
    check = doctor.Doctor(home=tmp_path, repo=ROOT, output=lines.append,
                          adapter_template=str(tmp_path / "linko-{name}-instance"))
    check.code_view("work")
    assert lines == ["PASS instance work code view has ai_manager, harnesses, README.md, RULES.md"]
    path.write_text(path.read_text().replace(json.dumps(str(ROOT / "harnesses"))[1:-1], "/elsewhere"))
    check.code_view("work")
    assert lines[-1].startswith("FAIL instance work code view") and "add harnesses to the mounts list" in lines[-1]
    check.code_view("missing")  # no adapter: the instance reads the checkout itself, nothing to report
    assert len(lines) == 2
