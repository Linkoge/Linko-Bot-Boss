"""Autopilot (ai_manager/autopilot.py): the two local locks, no way to enable it through the app, restricted instances
never, the goal loop's state machine, the wake-up ladder, checkpoints and the Settings panel's setup guide."""
import asyncio
import json
import re
import secrets
import subprocess
import time
from pathlib import Path

import pytest
from fake_codex import FakeServer
from fastapi.testclient import TestClient

from ai_manager import autopilot
from ai_manager.autopilot import (APPROVAL, CHECK_SECONDS, PASS, STALL_ROUNDS, Engine, Goals, Locks, Loop, confirmed,
                                  decide, effort_for, extend_tool, level_of, probe)
from ai_manager.config import Settings
from ai_manager.core import Tasks
from ai_manager.db import Database
from ai_manager.registry import Registry
from ai_manager.web import create_app

ROOT = Path(__file__).resolve().parents[1]
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32


class Run:
    """subprocess.run stand-in: records argv, answers `sudo -n true` with the configured result."""

    def __init__(self, sudo=True):
        self.sudo, self.calls = sudo, []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        code = 0 if argv[:3] != ["sudo", "-n", "true"] or self.sudo else 1
        return subprocess.CompletedProcess(argv, code, "", "")


def settings_for(tmp_path, **changes):
    root = tmp_path / "projects"
    (root / "one").mkdir(parents=True, exist_ok=True)
    return Settings(data_dir=tmp_path / "data", project_roots=(root,), secure_cookie=False,
                    env_file=tmp_path / "linko.env", **changes)


# Locks ----------------------------------------------------------------------------------------------------------------

@pytest.mark.smoke
@pytest.mark.parametrize("config,os_lock,restricted,level", [
    (False, False, False, "off"), (True, False, False, "partial"), (False, True, False, "partial"),
    (True, True, False, "full"), (True, True, True, "off"), (False, True, True, "off")])
def test_lock_matrix(tmp_path, config, os_lock, restricted, level):
    assert level_of(config, os_lock, restricted) == level
    project = tmp_path / "work"
    project.mkdir()
    settings = Settings(data_dir=tmp_path / "data", project_roots=(tmp_path,), autopilot="full" if config else "",
                        restricted=restricted, project_path=project if restricted else None)
    run = Run(sudo=os_lock)
    locks = Locks(settings, run)
    assert locks.level() == level and locks.config() == (config and not restricted)
    if restricted:
        assert run.calls == []  # a restricted instance never even probes sudo
    else:
        assert run.calls == [["sudo", "-n", "true"]]
        locks.level()
        assert len(run.calls) == 1  # cached between probes


def test_config_lock_is_only_the_environment(tmp_path, monkeypatch):
    monkeypatch.delenv("AI_MANAGER_AUTOPILOT", raising=False)
    assert Settings(data_dir=tmp_path).autopilot == ""
    monkeypatch.setenv("AI_MANAGER_AUTOPILOT", "yes")
    assert not Locks(Settings(data_dir=tmp_path), Run()).config()  # only the exact value "full"
    monkeypatch.setenv("AI_MANAGER_AUTOPILOT", "full")
    assert Locks(Settings(data_dir=tmp_path), Run()).config()


def test_sudo_probe_is_non_interactive_and_fails_closed():
    seen = []

    def run(argv, **kwargs):
        seen.append((argv, kwargs.get("timeout"), kwargs.get("stdin")))
        raise FileNotFoundError("sudo")
    assert autopilot.sudo_works(run) is False
    assert seen == [(["sudo", "-n", "true"], 5, subprocess.DEVNULL)]


def test_source_never_writes_a_sudoers_file():
    source = (ROOT / "ai_manager/autopilot.py").read_text()
    writes = [line for line in source.splitlines() if "SUDOERS" in line and ("write" in line or "open(" in line)]
    assert writes == []
    assert "sudoers.d" not in (ROOT / "ai_manager/web.py").read_text()


# API: owner-only, read-only about the locks --------------------------------------------------------------------------

def sign_in(client, settings):
    owner = {"username": "owner", "password": secrets.token_urlsafe(16)}
    token = (settings.data_dir / "setup-token").read_text()
    assert client.post("/api/setup", json={**owner, "token": token}).status_code == 200
    client.headers["X-CSRF-Token"] = client.post("/api/login", json=owner).json()["csrf"]


@pytest.fixture
def served(tmp_path, monkeypatch):
    def start(sudo=False, **changes):
        monkeypatch.setattr(autopilot, "sudo_works", lambda run=None: sudo)
        settings = settings_for(tmp_path, **changes)
        app = create_app(settings, FakeServer(settings))
        client = TestClient(app, base_url="http://localhost")
        client.__enter__()
        started.append(client)
        sign_in(client, settings)
        return client, app, settings
    started = []
    yield start
    for client in started:
        client.__exit__(None, None, None)


@pytest.mark.smoke
def test_off_by_default_and_no_route_can_enable_it(served):
    client, app, settings = served(sudo=True)
    assert client.get("/api/auth-status").json()["autopilot"] == "partial"  # sudo alone is not autopilot
    view = client.get("/api/autopilot").json()
    assert view["status"]["config"] is False and view["status"]["level"] == "partial"
    for method, path, body in (("put", "/api/autopilot", {"level": "full"}), ("post", "/api/autopilot", {"level": "full"}),
                               ("patch", "/api/autopilot", {"config": True})):
        assert getattr(client, method)(path, json=body).status_code == 405
    assert client.post("/api/autopilot/pause", json={"paused": False, "level": "full"}).status_code == 422
    for key in ("autopilot", "AI_MANAGER_AUTOPILOT", "autopilot_level"):
        app.state.db.set_setting(key, "full")
    assert client.get("/api/autopilot").json()["status"]["level"] == "partial"
    project = app.state.registry.add(settings.project_roots[0] / "one")
    refused = client.post("/api/autopilot/goals", json={"project_id": project["id"], "title": "Desktop",
                                                        "done_when": ["wallpaper is blue"]})
    assert refused.status_code == 409 and "AI_MANAGER_AUTOPILOT=full" in refused.json()["detail"]
    routes = {(method, route.path) for route in app.routes if route.path.startswith("/api/autopilot")
              for method in getattr(route, "methods", ())}
    assert routes == {("GET", "/api/autopilot"), ("POST", "/api/autopilot/pause"), ("POST", "/api/autopilot/goals"),
                      ("POST", "/api/autopilot/goals/{goal_id}/images"), ("POST", "/api/autopilot/goals/{goal_id}/{op}"),
                      ("GET", "/api/autopilot/goals/{goal_id}/journal")}


def test_auth_status_hides_the_level_before_sign_in(served):
    client, _, _ = served(autopilot="full", sudo=True)
    assert client.get("/api/auth-status").json()["autopilot"] == "full"
    client.cookies.clear()
    assert client.get("/api/auth-status").json()["autopilot"] is None


def test_restricted_instance_is_never_autopilot(tmp_path, monkeypatch):
    monkeypatch.setattr(autopilot, "sudo_works", lambda run=None: pytest.fail("a restricted instance probed sudo"))
    project = tmp_path / "work" / "folder"
    project.mkdir(parents=True)
    settings = Settings(data_dir=tmp_path / "work/data", project_roots=(tmp_path / "work",), restricted=True,
                        project_path=project, secure_cookie=False, autopilot="full")
    app = create_app(settings, FakeServer(settings))
    with TestClient(app, base_url="http://localhost") as client:
        sign_in(client, settings)
        assert client.get("/api/auth-status").json()["autopilot"] == "off"
        assert client.get("/api/autopilot").status_code == 403
        assert client.post("/api/autopilot/pause", json={"paused": False}).status_code == 403
        assert client.post("/api/autopilot/goals", json={"project_id": "x", "title": "t", "done_when": ["a"]}).status_code == 403
        assert app.state.chat.autopilot.config() is False
    css = (ROOT / "ai_manager/static/style.css").read_text()
    hidden = re.search(r"\.restricted #view-settings>article((?::not\(#[\w-]+\))+)", css).group(1)
    assert "#autopilot-panel" not in hidden  # the panel stays hidden on instances


def test_setup_guide_has_this_machines_exact_lines(served):
    client, _, settings = served(sudo=False)
    settings.env_file.write_text("AI_MANAGER_PORT=8787\n")
    guide = client.get("/api/autopilot").json()["guide"]
    user = guide["user"]
    assert guide["env_line"] == "AI_MANAGER_AUTOPILOT=full" and guide["env_file"] == str(settings.env_file)
    assert guide["env_file_exists"] is True and guide["env_line_present"] is False
    assert guide["sudoers_path"] == "/etc/sudoers.d/linko-autopilot"
    assert guide["sudoers_content"].splitlines()[0] == f"{user} ALL=(ALL:ALL) NOPASSWD: ALL"
    install = guide["sudoers_install"].splitlines()
    assert install.index("sudo visudo -cf /tmp/linko-autopilot") < next(
        n for n, line in enumerate(install) if line.startswith("sudo install"))  # validated before it is installed
    assert guide["sudoers_validate"] == "sudo visudo -cf /etc/sudoers.d/linko-autopilot"
    assert "NoNewPrivileges=no" in guide["override_install"] and "etckeeper" in guide["etckeeper_install"]
    assert guide["snapshot_line"].startswith("AI_MANAGER_SNAPSHOT_CMD=")
    off = guide["off"]
    assert "sed -i '/^AI_MANAGER_AUTOPILOT=/d'" in off and "sudo rm -f /etc/sudoers.d/linko-autopilot" in off
    assert len(guide["approvals"]) == 5
    settings.env_file.write_text("AI_MANAGER_AUTOPILOT=full\n")
    assert client.get("/api/autopilot").json()["guide"]["env_line_present"] is True


def test_settings_panel_has_a_copy_button_for_every_command():
    html = (ROOT / "ai_manager/static/index.html").read_text()
    panel = re.search(r'<article class="panel" id="autopilot-panel".*?</article>', html).group(0)
    blocks = re.findall(r'<code id="(autopilot-[\w-]+)"></code></pre><button type="button" data-copy="\1"', panel)
    assert set(blocks) == {"autopilot-env-line", "autopilot-env-command", "autopilot-sudoers-content",
                           "autopilot-sudoers-install", "autopilot-sudoers-validate", "autopilot-override-install",
                           "autopilot-restart", "autopilot-etckeeper-install", "autopilot-snapshot-line",
                           "autopilot-screenshot-line", "autopilot-off"}
    for text in ("Config lock", "OS lock", "visudo", "etckeeper", "snapshot", "Turn it off", 'id="autopilot-pause"',
                 'id="autopilot-env-file"', 'id="autopilot-goal-form"', 'id="autopilot-goals"'):
        assert text in panel
    assert 'id="autopilot-badge"' in html and '/static/autopilot.js' in html


def test_goals_through_the_api_with_images_pause_and_journal(served):
    client, app, settings = served(autopilot="full", sudo=True)
    project = app.state.registry.add(settings.project_roots[0] / "one")
    goal = client.post("/api/autopilot/goals", json={
        "project_id": project["id"], "title": "Blue desktop", "description": "Make the desktop calm",
        "done_when": ["wallpaper is blue", " ", "dock on the left"], "daily_budget": 50000,
        "deadline": "2099-01-31"}).json()
    assert goal["checklist"] == ["wallpaper is blue", "dock on the left"] and goal["status"] == "active"
    image = client.post(f"/api/autopilot/goals/{goal['id']}/images?name=ref.png", content=PNG)
    assert image.status_code == 200 and image.json()["images"] == [{"name": "ref.png"}]
    assert client.post(f"/api/autopilot/goals/{goal['id']}/images?name=x.txt", content=b"hello").status_code == 409
    assert client.post(f"/api/autopilot/goals/{goal['id']}/pause").json()["status"] == "paused"
    assert client.post(f"/api/autopilot/goals/{goal['id']}/resume").json()["status"] == "active"
    assert client.post(f"/api/autopilot/goals/{goal['id']}/explode").status_code == 422
    journal = client.get(f"/api/autopilot/goals/{goal['id']}/journal").json()["journal"]
    assert [row["kind"] for row in journal] == ["resume", "pause", "created"]
    assert client.post("/api/autopilot/pause", json={"paused": True}).json()["paused"] is True
    listed = client.get("/api/autopilot").json()["goals"]
    assert [g["id"] for g in listed] == [goal["id"]] and listed[0]["last_journal"]["kind"] == "resume"
    bad = client.post("/api/autopilot/goals", json={"project_id": project["id"], "title": "x", "done_when": ["a"],
                                                    "deadline": "soon"})
    assert bad.status_code == 409


# State machine --------------------------------------------------------------------------------------------------------

@pytest.mark.smoke
def test_improving_rounds_never_stop_and_reset_the_stall():
    loop = Loop()
    for round_ in range(1, 12):  # no round cap while the score improves
        assert decide(loop, round_ / 100, failed=False) == ("continue", True)
    assert loop.stall == 0 and loop.best == 0.11


@pytest.mark.smoke
def test_stall_then_new_approach_then_pause():
    loop = Loop(best=0.5)
    decisions = [decide(loop, 0.5, failed=False)[0] for _ in range(STALL_ROUNDS)]
    assert decisions == ["continue"] * (STALL_ROUNDS - 1) + ["new_approach"]
    assert loop.approach == 2 and loop.stall == 0 and loop.stalls == 1
    assert decide(loop, 0.6, failed=False) == ("continue", True)  # the new approach helped once
    decisions = [decide(loop, 0.4, failed=False)[0] for _ in range(STALL_ROUNDS)]
    assert decisions == ["continue"] * (STALL_ROUNDS - 1) + ["pause_stalled"]


def test_budget_and_deadline_pause():
    now = time.time()
    loop = Loop(usage_day=autopilot.today(now), usage=10000)
    assert decide(loop, 0.95, False, {"daily_budget": 10000, "deadline": None}, now) == ("pause_budget", True)
    assert autopilot.over_budget({"daily_budget": 10000, "deadline": None}, Loop(usage_day="2000-01-01", usage=10**6),
                                 now) == ""  # yesterday's spending does not count
    assert autopilot.over_budget({"daily_budget": None, "deadline": now - 1}, Loop(), now) == "deadline reached"


@pytest.mark.smoke
def test_done_needs_ninety_percent_and_the_final_check():
    loop = Loop(best=0.5)
    assert decide(loop, 0.8, False)[0] == "continue"
    assert decide(loop, PASS, False)[0] == "confirm" and loop.phase == "confirm"
    assert confirmed(loop, False) == "continue" and loop.phase == "plan" and loop.wake
    assert decide(loop, 1.0, False)[0] == "confirm"
    assert confirmed(loop, True) == "done" and loop.phase == "done" and loop.wake is False


def test_effort_escalation():
    assert effort_for("plan", Loop(), rounds=0) == "medium"  # the first plan
    assert effort_for("plan", Loop(), rounds=3) == "low"
    assert effort_for("plan", Loop(failures=2), rounds=3) == "medium"  # after 2 consecutive failures
    assert effort_for("verify", Loop(failures=5), rounds=3) == "low"
    assert effort_for("confirm", Loop(), rounds=3) == "medium"
    loop = Loop()
    decide(loop, 0, failed=True)
    decide(loop, 0, failed=True)
    assert loop.failures == 2
    decide(loop, 0, failed=False)
    assert loop.failures == 0


# Wake-up ladder -------------------------------------------------------------------------------------------------------

@pytest.fixture
def world(tmp_path):
    settings = settings_for(tmp_path, autopilot="full")
    settings.prepare()
    db = Database(settings.db_path)
    registry = Registry(db, settings.project_roots)
    project = registry.add(settings.project_roots[0] / "one")
    return settings, db, registry, project


def add_task(db, project, state="running", held=0):
    task = Tasks(db).add(project["id"], "step " + secrets.token_hex(4), None, "autopilot:" + secrets.token_hex(4))
    db.run("UPDATE tasks SET state=?,held=? WHERE id=?", (state, held, task["id"]))
    return task["id"]


def add_execution(db, project, task_id, output="", pid=None, finished=False):
    execution = secrets.token_hex(8)
    db.run("""INSERT INTO executions(id,task_id,project_id,agent_id,status,output,pid,started_at,finished_at,delivery)
        VALUES(?,?,?,'worker','running',?,?,?,?,?)""", (execution, task_id, project["id"], output, pid, time.time(),
                                                        time.time() if finished else None,
                                                        "pending" if finished else None))
    return execution


def test_probe_decisions(world):
    _, db, _, project = world
    now = time.time()
    goal = lambda task_id: {"task_id": task_id}
    assert probe(db, goal(add_task(db, project, "done")), Loop(), now)[0] == "finished"
    assert probe(db, goal(add_task(db, project, "queued")), Loop(), now)[0] == "progressing"
    assert probe(db, goal(add_task(db, project, "running", held=1)), Loop(), now)[0] == "stuck"
    task = add_task(db, project)
    execution = add_execution(db, project, task, "working", pid=4242)
    verdict, seen = probe(db, goal(task), Loop(), now, alive=lambda pid: True)
    assert verdict == "progressing" and seen["execution"] == execution  # first look at this execution
    loop = Loop(probe=seen)
    verdict, seen = probe(db, goal(task), loop, now, alive=lambda pid: True)
    assert verdict == "unclear" and seen["quiet"] == 1  # nothing new for one interval: ask Haiku
    loop.probe = seen
    assert probe(db, goal(task), loop, now, alive=lambda pid: True)[0] == "stuck"  # two quiet intervals
    db.run("UPDATE executions SET output='working more' WHERE id=?", (execution,))
    assert probe(db, goal(task), loop, now, alive=lambda pid: True)[0] == "progressing"  # the log grew
    assert probe(db, goal(task), Loop(probe=seen), now, alive=lambda pid: False)[0] == "stuck"  # process gone


class Asks:
    """Scripted model replies by purpose; records (purpose, effort, image count)."""

    def __init__(self, **replies):
        self.replies = {k: list(v) for k, v in replies.items()}
        self.calls = []

    async def __call__(self, purpose, effort, text, images=()):
        self.calls.append((purpose, effort, len(images)))
        reply = self.replies[purpose].pop(0)
        return json.dumps(reply), "opus" if purpose != "triage" else "haiku", effort


class Pipeline:
    def __init__(self):
        self.pushed = []
        self.backups = self

    def request(self, project_id):
        self.pushed.append(project_id)


def engine_for(world, asks, run=None, screenshot=None, sudo=True, **settings_changes):
    settings, db, registry, project = world
    for name, value in settings_changes.items():
        setattr(settings, name, value)
    run = run or Run(sudo=sudo)
    delegated, cancelled = [], []

    async def delegate(goal, step, acceptance, key):
        task = Tasks(db).add(goal["project_id"], step, None, key, acceptance=acceptance,
                             selected={"constraints": list(autopilot.WORKER_RULES)}, reuse_objective=False)
        delegated.append((step, key))
        return task["id"]

    async def cancel(task_id):
        cancelled.append(task_id)
        db.run("UPDATE tasks SET state='failed' WHERE id=?", (task_id,))

    engine = Engine(db, registry, settings, pipeline=Pipeline(), locks=Locks(settings, run), ask=asks,
                    delegate=delegate, cancel=cancel, run=run, screenshot=screenshot or (lambda path: None))
    engine.delegated, engine.cancelled = delegated, cancelled
    return engine


async def settle(engine):
    await engine.tick()
    await asyncio.gather(*engine.jobs.values())


def finish(db, project, task_id, output="done", state="done"):
    add_execution(db, project, task_id, output, finished=True)
    db.run("UPDATE tasks SET state=?,lease_owner=NULL WHERE id=?", (state, task_id))


def plan(step="set the wallpaper", **extra):
    return {"step": step, "acceptance": "screenshot shows it", "risky": False, "needs_owner": "", "why": "first",
            **extra}


def scores(*passes, next_idea="next"):
    return {"items": [{"pass": p, "note": ""} for p in passes], "summary": "checked", "next": next_idea}


@pytest.mark.smoke
def test_full_loop_to_done_with_checkpoints_and_journal(world, tmp_path):
    settings, db, _, project = world
    shots = []

    def screenshot(path):
        shots.append(path)
        Path(path).write_bytes(PNG)
        return Path(path)
    asks = Asks(plan=[plan(), plan("move the dock left")], verify=[scores(True, False), scores(True, True)],
                confirm=[{"done": True, "reason": "matches the reference"}])
    run = Run()
    engine = engine_for(world, asks, run=run, screenshot=screenshot, snapshot_cmd="snap-vm --fast")
    goals = engine.goals
    goal = goals.create(project["id"], "Calm desktop", "", ["wallpaper is blue", "dock on the left"])
    goals.add_image(goal["id"], "ref.png", PNG)
    asyncio.run(settle(engine))
    row = goals.get(goal["id"])
    assert Loop.load(row["loop"]).phase == "working" and row["rounds"] == 1 and row["task_id"]
    assert asks.calls == [("plan", "medium", 0)]  # first plan on medium
    assert ["snap-vm", "--fast", goal["id"], "goal-start"] in run.calls
    task = Tasks(db).get(row["task_id"])
    assert task["request_key"].startswith("autopilot:" + goal["id"])
    asyncio.run(settle(engine))
    assert len(asks.calls) == 1  # still working and not yet due: nothing happens, zero tokens
    finish(db, project, row["task_id"])
    asyncio.run(settle(engine))  # finished task wakes the goal at once: verify with reference + desktop screenshot
    assert asks.calls[-1] == ("verify", "low", 2) and len(shots) == 1
    row = goals.get(goal["id"])
    assert row["score"] == 0.5 and engine.pipeline.pushed == [project["id"]]  # improved -> checkpoint push
    assert db.one("SELECT delivery FROM executions WHERE task_id=?", (row["task_id"],))["delivery"] == "autopilot"
    asyncio.run(settle(engine))  # next plan, low effort now
    assert asks.calls[-1] == ("plan", "low", 0)
    finish(db, project, goals.get(goal["id"])["task_id"])
    asyncio.run(settle(engine))  # 100% -> confirm phase
    asyncio.run(settle(engine))  # the final check on medium
    assert asks.calls[-2:] == [("verify", "low", 2), ("confirm", "medium", 2)]
    final = goals.view(goal["id"])
    assert final["status"] == "done" and final["score"] == 1.0 and final["wake"] is False
    assert final["next_wake"] is None and final["last_journal"]["kind"] == "done"
    kinds = [r["kind"] for r in goals.journal(goal["id"])]
    assert kinds == ["done", "verify", "step", "verify", "step", "created"]
    verify = next(r for r in goals.journal(goal["id"]) if r["kind"] == "verify")
    assert [s["pass"] for s in verify["scores"]] == [True, True] and verify["next_idea"] == "next"
    calls = len(asks.calls)
    asyncio.run(settle(engine))
    assert len(asks.calls) == calls  # done: its wake-ups are off


def test_stall_new_approach_then_pause_asks_the_owner(world):
    _, db, _, project = world
    rounds = 2 * STALL_ROUNDS + 1
    asks = Asks(plan=[plan(f"try {n}") for n in range(rounds)], verify=[scores(True, False)] * rounds)
    engine = engine_for(world, asks)
    goal = engine.goals.create(project["id"], "Goal", "", ["a", "b"])
    for _ in range(rounds):
        asyncio.run(settle(engine))
        finish(db, project, engine.goals.get(goal["id"])["task_id"])
        asyncio.run(settle(engine))
        if engine.goals.get(goal["id"])["status"] != "active":
            break
    row = engine.goals.view(goal["id"])
    assert row["status"] == "paused" and "your input is needed" in row["pause_reason"]
    assert row["approach"] == 2 and row["rounds"] == 1 + 2 * STALL_ROUNDS
    assert any("New approach 2" in (r["next_idea"] or "") for r in engine.goals.journal(goal["id"], 50))
    assert db.one("SELECT 1 FROM events WHERE kind='autopilot.paused'")


def test_budget_stops_and_saves_progress(world):
    _, db, _, project = world
    asks = Asks(plan=[plan()], verify=[scores(True, False)])
    engine = engine_for(world, asks)
    goal = engine.goals.create(project["id"], "Goal", "", ["a", "b"], daily_budget=1000)
    asyncio.run(settle(engine))
    task_id = engine.goals.get(goal["id"])["task_id"]
    finish(db, project, task_id)
    db.run("UPDATE executions SET usage=? WHERE task_id=?", (json.dumps({"turn": {"totalTokens": 5000}}), task_id))
    asyncio.run(settle(engine))
    row = engine.goals.view(goal["id"])
    assert row["status"] == "paused" and "daily budget" in row["pause_reason"] and row["score"] == 0.5
    assert row["used_today"] >= 5000


def test_needs_owner_approval_waits(world):
    _, db, _, project = world
    asks = Asks(plan=[plan(needs_owner="buy a paid theme"), plan()], verify=[])
    engine = engine_for(world, asks)
    from ai_manager.chat import Chat
    conversation = Chat(db, world[2], FakeServer(world[0])).conversation(project["id"])
    goal = engine.goals.create(project["id"], "Goal", "", ["a"])
    asyncio.run(settle(engine))
    row = engine.goals.view(goal["id"])
    assert row["status"] == "waiting" and "buy a paid theme" in row["pause_reason"] and not engine.delegated
    told = db.one("SELECT content FROM messages WHERE conversation_id=? AND role='assistant'", (conversation["id"],))
    assert told["content"].startswith('Autopilot is waiting on "Goal": Needs your approval: buy a paid theme')
    engine.goals.control(goal["id"], "resume")
    asyncio.run(settle(engine))
    finish(db, project, engine.goals.get(goal["id"])["task_id"], output=f"{APPROVAL} publish to the store")
    asyncio.run(settle(engine))
    row = engine.goals.view(goal["id"])
    assert row["status"] == "waiting" and "publish to the store" in row["pause_reason"]


def test_risky_step_takes_a_snapshot_and_etckeeper_commits(world, monkeypatch):
    _, db, _, project = world
    monkeypatch.setattr(autopilot.shutil, "which", lambda name: "/usr/bin/" + name)
    asks = Asks(plan=[plan("switch the display manager to lightdm")])
    run = Run()
    engine = engine_for(world, asks, run=run, snapshot_cmd="")
    goal = engine.goals.create(project["id"], "Goal", "", ["a"])
    asyncio.run(settle(engine))
    steps = [r for r in engine.goals.journal(goal["id"]) if r["kind"] == "step"]
    assert "no snapshot hook configured" in steps[0]["result"]  # logged; the step still went ahead
    assert engine.delegated and Loop.load(engine.goals.get(goal["id"])["loop"]).risky
    assert any(call[:4] == ["sudo", "-n", "etckeeper", "commit"] for call in run.calls)


def test_twenty_minute_check_spends_no_tokens_while_progressing(world):
    settings, db, _, project = world
    asks = Asks(plan=[plan()], triage=[{"verdict": "stuck"}], stuck=[{"decision": "cancel", "reason": "hung"}])
    engine = engine_for(world, asks)
    goal = engine.goals.create(project["id"], "Goal", "", ["a"])
    asyncio.run(settle(engine))
    task_id = engine.goals.get(goal["id"])["task_id"]
    db.run("UPDATE tasks SET state='running' WHERE id=?", (task_id,))
    execution = add_execution(db, project, task_id, "log", pid=None)
    due = lambda: db.run("UPDATE autopilot_goals SET next_wake=0 WHERE id=?", (goal["id"],))
    due()
    asyncio.run(settle(engine))  # first look: progressing, no model call
    assert [c[0] for c in asks.calls] == ["plan"]
    assert engine.goals.get(goal["id"])["next_wake"] > time.time() + CHECK_SECONDS - 60
    db.run("UPDATE executions SET output='log grows' WHERE id=?", (execution,))
    due()
    asyncio.run(settle(engine))
    assert [c[0] for c in asks.calls] == ["plan"]  # the log grew: still zero tokens
    due()
    asyncio.run(settle(engine))  # quiet: Haiku says stuck -> the Opus manager cancels the step
    assert [c[0] for c in asks.calls] == ["plan", "triage", "stuck"] and engine.cancelled == [task_id]


def test_engine_is_idle_without_full_autopilot(world):
    settings, db, _, project = world
    asks = Asks()
    goal = Goals(db).create(project["id"], "Goal", "", ["a"])
    for sudo, config in ((False, "full"), (True, "")):
        engine = engine_for(world, asks, sudo=sudo, autopilot=config)
        asyncio.run(settle(engine))
        assert asks.calls == [] and not engine.delegated
    engine = engine_for(world, asks, autopilot="full")
    db.set_setting(autopilot.PAUSED, True)  # the owner's pause button
    asyncio.run(settle(engine))
    assert asks.calls == [] and Goals(db).get(goal["id"])["rounds"] == 0


# Chat -----------------------------------------------------------------------------------------------------------------

def test_chat_tool_and_context_only_with_the_config_lock(world):
    settings, db, _, project = world
    from ai_manager.orchestration import MANAGER_TOOL
    tool = extend_tool(MANAGER_TOOL)
    assert "autopilot_goal" in tool["inputSchema"]["properties"]["action"]["enum"]
    assert "autopilot_goal" not in MANAGER_TOOL["inputSchema"]["properties"]["action"]["enum"]  # unchanged elsewhere
    locks = Locks(settings, Run())
    goals = Goals(db, data_dir=settings.data_dir)
    with pytest.raises(ValueError, match="owner's own message"):
        autopilot.chat_action(goals, locks, {"goal": {"op": "list"}}, project["id"], internal=True)
    image = settings.data_dir / "upload.png"
    image.write_bytes(PNG)
    created = autopilot.chat_action(goals, locks, {"goal": {"title": "Dark theme", "done_when": ["dark"]}},
                                    project["id"], internal=False,
                                    attachments=[{"name": "ref.png", "path": str(image), "native_image": True}])
    assert created["images"] == [{"name": "ref.png"}] and created["project_id"] == project["id"]
    assert autopilot.context(goals, locks, project["id"])["goals"][0]["title"] == "Dark theme"
    settings.autopilot = ""
    with pytest.raises(ValueError, match="off on this machine"):
        autopilot.chat_action(goals, locks, {"goal": {"op": "list"}}, project["id"], internal=False)


def test_autopilot_step_results_skip_the_chat(served):
    client, app, settings = served(sudo=False)
    db = app.state.db
    project = app.state.registry.add(settings.project_roots[0] / "one")
    task = Tasks(db).add(project["id"], "autopilot step", None, "autopilot:goal:1:1")
    db.run("UPDATE tasks SET state='done' WHERE id=?", (task["id"],))
    db.run("""INSERT INTO executions(id,task_id,project_id,agent_id,status,started_at,finished_at,delivery)
        VALUES('e1',?,?,'worker','completed',0,1,'pending')""", (task["id"], project["id"]))
    asyncio.run(app.state.chat.deliver_results())
    assert db.one("SELECT delivery FROM executions WHERE id='e1'")["delivery"] == "pending"
    assert not db.one("SELECT 1 FROM messages WHERE role='worker'")


def test_checkpoint_and_screenshot_hooks(tmp_path):
    run = Run()
    assert "not installed" in autopilot.etckeeper("msg", run, which=lambda name: None) and run.calls == []
    assert autopilot.snapshot("", "g1", "goal-start", run).startswith("no snapshot hook configured")
    assert autopilot.snapshot("ssh root@hv /usr/local/bin/snap 'botos vm'", "g1", "risky-step", run) == \
        "snapshot taken (risky-step)"
    assert run.calls[-1] == ["ssh", "root@hv", "/usr/local/bin/snap", "botos vm", "g1", "risky-step"]  # no shell
    settings = Settings(data_dir=tmp_path, screenshot_cmd="my-shot --root", autopilot_display=":1")
    seen = []

    def shot(argv, **kwargs):
        seen.append((argv, kwargs["env"]["DISPLAY"]))
        Path(argv[-1]).write_bytes(PNG)
        return subprocess.CompletedProcess(argv, 0)
    target = tmp_path / "desktop.png"
    assert autopilot.desktop_screenshot(settings, target, shot, which=lambda name: None) == target
    assert seen == [(["my-shot", "--root", str(target)], ":1")]
    assert autopilot.desktop_screenshot(Settings(data_dir=tmp_path), tmp_path / "none.png", shot,
                                        which=lambda name: None) is None  # no tool: logged by the engine
