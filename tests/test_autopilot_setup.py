"""scripts/autopilot_setup.sh in a temporary root, without real sudo: --root prefixes every system path, and fake
visudo / systemctl / apt-get / etckeeper record their calls. Setup is idempotent, validates the sudoers drop-in before
installing it, restarts only this machine's unit, refuses restricted instances and Main, and --remove undoes it."""
import os
import pwd
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "autopilot_setup.sh"
USER = pwd.getpwuid(os.getuid()).pw_name
HOME = pwd.getpwuid(os.getuid()).pw_dir


class Machine:
    def __init__(self, tmp_path):
        self.root, self.bin, self.log = tmp_path / "root", tmp_path / "bin", tmp_path / "calls.log"
        self.bin.mkdir()
        self.log.write_text("")
        for name in ("visudo", "systemctl", "apt-get", "etckeeper"):
            self.tool(name)
        self.env_file = self.root / HOME.lstrip("/") / ".config/linko-bot-manager.env"
        self.sudoers = self.root / "etc/sudoers.d/linko-autopilot"
        self.override = self.root / HOME.lstrip("/") / ".config/systemd/user/linko-bot-manager.service.d/autopilot.conf"

    def tool(self, name, code=0, body=""):
        path = self.bin / name
        path.write_text(f'#!/bin/sh\necho "{name} $*" >> "{self.log}"\n{body}\nexit {code}\n')
        path.chmod(0o755)
        return path

    def run(self, *args, answer=None, **env):
        environ = {k: v for k, v in os.environ.items() if not k.startswith(("AI_MANAGER_", "SUDO_"))}
        environ.update(PATH=f"{self.bin}:/usr/bin:/bin", VISUDO=str(self.bin / "visudo"),
                       SYSTEMCTL=str(self.bin / "systemctl"), APT_GET=str(self.bin / "apt-get"),
                       ETCKEEPER=str(self.bin / "etckeeper"))
        environ.update(env)
        return subprocess.run(["bash", str(SCRIPT), "--root", str(self.root), "--user", USER, *args],
                              input=answer, capture_output=True, text=True, timeout=60, env=environ)

    def calls(self):
        return self.log.read_text().splitlines()

    def files(self):
        return {p.relative_to(self.root): p.read_text() for p in self.root.rglob("*") if p.is_file()}


@pytest.fixture
def machine(tmp_path):
    return Machine(tmp_path)


def test_setup_installs_everything_and_is_idempotent(machine):
    first = machine.run("--yes")
    assert first.returncode == 0, first.stderr
    assert first.stdout.strip().endswith("Autopilot ready")
    assert machine.env_file.read_text() == "AI_MANAGER_AUTOPILOT=full\n"
    assert oct(machine.env_file.stat().st_mode & 0o777) == "0o600"
    assert f"{USER} ALL=(ALL:ALL) NOPASSWD: ALL" in machine.sudoers.read_text().splitlines()
    assert oct(machine.sudoers.stat().st_mode & 0o777) == "0o440"
    assert machine.override.read_text() == "[Service]\nNoNewPrivileges=no\n"
    calls = machine.calls()
    visudo = [c for c in calls if c.startswith("visudo -cf")]
    assert len(visudo) == 2 and visudo[0] != f"visudo -cf {machine.sudoers}" and visudo[1] == f"visudo -cf {machine.sudoers}"
    assert calls.index(visudo[0]) < calls.index(visudo[1])  # the staged copy is validated before it is installed
    assert "etckeeper commit baseline before autopilot" in calls and not any(c.startswith("apt-get") for c in calls)
    assert calls[-2:] == ["systemctl --user daemon-reload", "systemctl --user restart linko-bot-manager"]
    before = machine.files()
    machine.log.write_text("")
    second = machine.run("--yes")
    assert second.returncode == 0 and "already" in second.stdout and machine.files() == before
    assert not any(c.startswith("visudo") for c in machine.calls())  # unchanged drop-in: nothing re-installed


def test_setup_keeps_other_env_lines_and_adds_the_worker_and_unit(machine):
    machine.env_file.parent.mkdir(parents=True)
    machine.env_file.write_text("AI_MANAGER_PORT=8787\nAI_MANAGER_AUTOPILOT=off")  # no trailing newline
    result = machine.run("--yes", "--worker", "botwork", "--unit", "linko-bot-manager@vm")
    assert result.returncode == 0, result.stderr
    assert machine.env_file.read_text() == "AI_MANAGER_PORT=8787\nAI_MANAGER_AUTOPILOT=full\n"
    assert "botwork ALL=(ALL:ALL) NOPASSWD: ALL" in machine.sudoers.read_text()
    override = machine.root / HOME.lstrip("/") / ".config/systemd/user/linko-bot-manager@vm.service.d/autopilot.conf"
    assert override.is_file() and machine.calls()[-1] == "systemctl --user restart linko-bot-manager@vm"


def test_etckeeper_through_apt_or_skipped(machine):
    result = machine.run("--yes", ETCKEEPER="no-such-etckeeper")
    assert result.returncode == 0 and "apt-get install -y etckeeper" in machine.calls()
    machine.log.write_text("")
    result = machine.run("--yes", ETCKEEPER="no-such-etckeeper", APT_GET="no-such-apt")
    assert result.returncode == 0 and "skipped" in result.stdout and "Autopilot ready" in result.stdout


def test_rejected_drop_in_installs_nothing(machine):
    machine.tool("visudo", code=1)
    result = machine.run("--yes")
    assert result.returncode != 0 and "visudo rejected" in result.stderr
    assert machine.files() == {} and not any(c.startswith("systemctl") for c in machine.calls())


def test_dry_run_and_unconfirmed_change_nothing(machine):
    dry = machine.run("--dry-run")
    assert dry.returncode == 0 and "nothing changed" in dry.stdout
    refused = machine.run(answer="no\n")
    assert refused.returncode != 0 and "not confirmed" in refused.stderr
    assert machine.files() == {} and machine.calls() == []
    assert machine.run(answer="yes\n").returncode == 0 and machine.sudoers.is_file()


@pytest.mark.parametrize("where", ["env_file", "environment"])
@pytest.mark.parametrize("line", ["AI_MANAGER_RESTRICTED=1", "AI_MANAGER_MACHINE_ROLE=main"])
def test_refuses_restricted_instances_and_main(machine, where, line):
    name, value = line.split("=")
    machine.env_file.parent.mkdir(parents=True)
    machine.env_file.write_text(line + "\n" if where == "env_file" else "")
    result = machine.run("--yes", **({name: value} if where == "environment" else {}))
    assert result.returncode != 0 and "refused" in result.stderr
    assert not machine.sudoers.exists() and "AUTOPILOT" not in machine.env_file.read_text() and machine.calls() == []


def test_needs_root_without_a_test_root():
    if os.getuid() == 0:
        pytest.skip("running as root")
    result = subprocess.run(["bash", str(SCRIPT), "--user", USER, "--yes"], capture_output=True, text=True, timeout=60)
    assert result.returncode != 0 and "needs root" in result.stderr


def test_remove_undoes_everything(machine):
    machine.env_file.parent.mkdir(parents=True)
    machine.env_file.write_text("AI_MANAGER_PORT=8787\n")
    assert machine.run("--yes").returncode == 0
    machine.log.write_text("")
    dry = machine.run("--remove", "--dry-run")
    assert dry.returncode == 0 and machine.sudoers.is_file() and machine.calls() == []
    removed = machine.run("--remove")
    assert removed.returncode == 0 and removed.stdout.strip().endswith("Autopilot removed")
    assert machine.env_file.read_text() == "AI_MANAGER_PORT=8787\n"
    assert not machine.sudoers.exists() and not machine.override.exists() and not machine.override.parent.exists()
    assert machine.calls() == ["systemctl --user daemon-reload", "systemctl --user restart linko-bot-manager"]
    again = machine.run("--remove")
    assert again.returncode == 0 and "not present" in again.stdout
