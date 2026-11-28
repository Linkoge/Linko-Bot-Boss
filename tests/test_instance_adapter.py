"""Whole-checkout code view for instances as other users (scripts/instance_adapter.py, deploy/instance-adapter.py,
ai_manager/code_view.py inspect): plan writes nothing, apply migrates a legacy adapter idempotently and rolls back a
failed restart, add sets up a new profile, the rendered adapter mounts the whole checkout with secrets masked, the
deploy stops on an unreadable code view (and re-grants owned files), and the release commit survives the foreign-owned
code directory. No root: a temporary prefix and fake groupadd/usermod/chgrp/chmod/setfacl/systemctl/mount."""
import errno
import getpass
import grp
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ai_manager import code_view, registry

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import instance_adapter  # noqa: E402

ME = getpass.getuser()
MY_GROUP = grp.getgrgid(os.getgid()).gr_name  # exists and I am in it: stands in for linko-code
COMMIT = "a" * 40

FAKE = r'''#!{python}
import json, os, sys
name = os.path.basename(sys.argv[0])
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps([name] + sys.argv[1:]) + "\n")
if name == "systemctl" and sys.argv[1:2] == ["is-active"]:
    sys.exit(0 if os.environ.get("FAKE_ACTIVE") else 3)
if name == "systemctl" and sys.argv[1:2] == ["restart"]:
    sys.exit(1 if os.environ.get("FAKE_RESTART_FAILS") else 0)
if name == "mountpoint":
    sys.exit(1)
if name == "runuser":
    print("{commit}")
'''


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """Fake privileged commands first on PATH; returns a reader of the logged argv lists."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "fake"
    script.write_text(FAKE.replace("{python}", sys.executable).replace("{commit}", COMMIT))
    script.chmod(0o755)
    for name in ("groupadd", "usermod", "chgrp", "chmod", "setfacl", "systemctl", "mount", "umount", "mountpoint",
                 "runuser", "chown"):
        (bin_dir / name).symlink_to(script)
    log = tmp_path / "commands.log"
    log.write_text("")
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_LOG", str(log))

    def calls():
        return [json.loads(line) for line in log.read_text().splitlines()]
    calls.log = log
    return calls


def checkout(tmp_path):
    repo = tmp_path / "checkout"
    (repo / "ai_manager").mkdir(parents=True)
    (repo / "ai_manager" / "core.py").write_text("x = 1\n")
    (repo / "harnesses").mkdir()
    (repo / "harnesses" / "h.json").write_text("{}\n")
    (repo / "README.md").write_text("readme\n")
    (repo / "secrets").mkdir()
    (repo / "secrets" / "key").write_text("not for instances\n")
    (repo / ".env").write_text("TOKEN=not-for-instances\n")
    (repo / "prod.env").write_text("TOKEN=not-for-instances\n")
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "GIT_CONFIG_GLOBAL": os.devnull}
    subprocess.run(["git", "init", "-q", str(repo)], check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "add", "ai_manager", "harnesses", "README.md"], check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit",
                    "-qm", "init"], check=True, env=env)
    return repo


def legacy_adapter(path, repo, user, port=8799, data="/home/work/.local/share/linko-bot-manager-work"):
    """The shape of the old per-path adapter (kaxa's), with this test's paths."""
    mounts = [(str(repo / n), f"/opt/linko-bot-manager/{n}") for n in ("ai_manager", "README.md", "harnesses")]
    mounts.append((str(repo / ".venv/lib/python3.12/site-packages"),
                   "/opt/linko-runtime/venv/lib/python3.12/site-packages"))
    components = [p for p in Path(data).parts[1:]]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/usr/bin/python3\nimport subprocess,os,pwd,sys\nos.chdir('/')\n"
        f"mounts = {mounts!r}\n"
        "if len(sys.argv) == 1 or sys.argv[1] == 'pre':\n"
        "    if subprocess.run(['mountpoint','-q','/opt/linko-bot-manager']).returncode!=0:\n"
        "        subprocess.run(['mount','--bind','/var/lib/linko-work/shared','/opt/linko-bot-manager'],check=True)\n"
        f"    commit=subprocess.check_output(['/usr/sbin/runuser','-u','{ME}','--','git','rev-parse','HEAD'])\n"
        "if len(sys.argv)>1 and sys.argv[1]=='post':\n"
        f"    url='http://127.0.0.1:{port}/api/auth-status'\n"
        f"who=pwd.getpwnam('{user}')\n"
        f"for component in {components!r}:\n    pass\n")
    path.chmod(0o755)
    return path


def args(tmp_path, repo, *extra):
    return ["--root", str(tmp_path / "root"), "--checkout", str(repo), "--data", str(tmp_path / "main"),
            "--group", MY_GROUP, *extra]


def tree(path):
    return {str(p.relative_to(path)): p.read_bytes() if p.is_file() else None for p in sorted(path.rglob("*"))}


def test_plan_writes_nothing_and_apply_migrates_a_legacy_adapter_once(tmp_path, fake, monkeypatch):
    repo = checkout(tmp_path)
    layout = instance_adapter.Layout(tmp_path / "root")
    old = legacy_adapter(layout.adapter("work"), repo, ME).read_text()
    layout.unit("work").parent.mkdir(parents=True)
    layout.unit("work").write_text("[Service]\n")
    before, out = tree(tmp_path / "root"), []
    assert instance_adapter.main(["plan", "work", *args(tmp_path, repo)], out.append) == 0
    assert tree(tmp_path / "root") == before and fake() == []  # plan: nothing written, nothing run
    text = "\n".join(out)
    assert "per-path bind list (legacy)" in text and f"code view /opt/linko-bot-manager = {repo} read-only" in text
    assert "never .env, .git, prod.env, secrets" in text and "apply with: sudo python3" in text

    monkeypatch.setenv("FAKE_ACTIVE", "1")
    out = []
    assert instance_adapter.main(["apply", "work", *args(tmp_path, repo)], out.append) == 0
    adapter = layout.adapter("work")
    settings = code_view.adapter_settings(adapter)
    assert settings["LAYOUT"] == code_view.WHOLE and settings["CHECKOUT"] == str(repo)
    assert settings["CODE"] == "/opt/linko-bot-manager" and settings["STATE"] == "/var/lib/linko-work/shared"
    assert settings["PORT"] == 8799 and settings["USER"] == ME and settings["GROUP"] == MY_GROUP
    assert settings["mounts"][0] == (str(repo), "/opt/linko-bot-manager")
    assert settings["mounts"][1:] == [(str(repo / ".venv/lib/python3.12/site-packages"),
                                       "/opt/linko-runtime/venv/lib/python3.12/site-packages")]
    assert os.stat(adapter).st_mode & 0o777 == 0o755
    assert Path(str(adapter) + ".legacy").read_text() == old  # kept for the rollback
    assert layout.dropin("work").read_text().endswith(f"SupplementaryGroups={MY_GROUP}\n")
    assert layout.unit("work").read_text() == "[Service]\n"  # an existing unit is never rewritten
    calls = fake()
    names = [c[0] for c in calls]
    assert "groupadd" not in names and "usermod" not in names  # the group exists and I am in it
    setgid = next(c for c in calls if c[:2] == ["chmod", "g+s"])
    assert str(repo) in setgid and str(repo / "ai_manager") in setgid and str(repo / "secrets") not in setgid
    recursive = next(c for c in calls if c[:3] == ["setfacl", "-P", "-R"])
    acl = f"g:{MY_GROUP}:rX,d:g:{MY_GROUP}:rX"
    assert recursive[4] == acl and str(repo / "ai_manager") in recursive
    assert not {str(repo / n) for n in (".git", ".env", "prod.env", "secrets")} & set(recursive)
    assert ["setfacl", "-P", "-m", acl, str(repo)] in calls  # the root's default ACL: new top-level entries share
    assert calls[-3:] == [["systemctl", "daemon-reload"], ["systemctl", "is-active", "--quiet",
                          "linko-bot-manager@work.service"], ["systemctl", "restart", "linko-bot-manager@work.service"]]
    assert "restarted healthy" in out[-1]
    # The deploy reads the new adapter as migrated; the legacy RUNTIME_PATHS check still passes on it too.
    assert code_view.check(repo, "work", adapter)[0] == []
    assert code_view.inspect(repo, "work", adapter).layout == code_view.WHOLE

    fake.log.write_text("")
    out = []
    assert instance_adapter.main(["apply", "work", *args(tmp_path, repo)], out.append) == 0
    assert out[-1] == "nothing changed: no restart"
    assert not [c for c in fake() if c[0] == "systemctl"]  # idempotent: ACL upkeep only
    assert Path(str(adapter) + ".legacy").read_text() == old


def test_a_failed_restart_restores_the_old_adapter(tmp_path, fake, monkeypatch):
    repo = checkout(tmp_path)
    layout = instance_adapter.Layout(tmp_path / "root")
    old = legacy_adapter(layout.adapter("work"), repo, ME).read_text()
    monkeypatch.setenv("FAKE_ACTIVE", "1")
    monkeypatch.setenv("FAKE_RESTART_FAILS", "1")
    out = []
    assert instance_adapter.main(["apply", "work", *args(tmp_path, repo)], out.append) == 1
    assert layout.adapter("work").read_text() == old
    calls = fake()
    assert ["umount", "-R", "/opt/linko-bot-manager"] in calls
    assert [c for c in calls if c[:2] == ["systemctl", "restart"]] == [["systemctl", "restart",
                                                                       "linko-bot-manager@work.service"]] * 2
    assert "restoring" in "\n".join(out)


def test_apply_needs_root_and_names_the_command(tmp_path, fake, monkeypatch):
    repo = checkout(tmp_path)
    layout = instance_adapter.Layout(tmp_path / "root")
    old = legacy_adapter(layout.adapter("work"), repo, ME).read_text()
    monkeypatch.setattr(instance_adapter, "SYSTEM", tmp_path / "root")  # as if this were /
    monkeypatch.setattr(instance_adapter.os, "geteuid", lambda: 1000)
    out = []
    assert instance_adapter.main(["apply", "work", *args(tmp_path, repo)], out.append) == 2
    assert out[-1].startswith("apply needs root: sudo python3 ") and out[-1].endswith("instance_adapter.py apply work")
    assert fake() == [] and layout.adapter("work").read_text() == old


def test_add_sets_up_a_new_profile_with_the_same_layout(tmp_path, fake):
    repo = checkout(tmp_path)
    python = tmp_path / "python"
    (python / "bin").mkdir(parents=True)
    (python / "bin" / "python3").write_text("")
    (repo / ".venv/lib/python3.12/site-packages").mkdir(parents=True)
    (repo / ".venv/pyvenv.cfg").write_text(f"home = {python / 'bin'}\nversion = 3.12\n")
    out = []
    assert instance_adapter.main(["add", "new", *args(tmp_path, repo, "--user", ME, "--port", "9123")], out.append) == 0
    layout = instance_adapter.Layout(tmp_path / "root")
    settings = code_view.adapter_settings(layout.adapter("new"))
    assert settings["CODE"] == "/opt/linko-new/code" and settings["PORT"] == 9123 and settings["USER"] == ME
    assert (str(python), instance_adapter.PYTHON_TARGET) in settings["mounts"]
    unit = layout.unit("new").read_text()
    for line in (f"User={ME}", f"SupplementaryGroups={MY_GROUP}", "WorkingDirectory=/opt/linko-new/code",
                 "ExecStartPre=+/usr/local/libexec/linko-new-instance pre",
                 "ExecStart=/opt/linko-runtime/python/bin/python3 -m ai_manager.cli serve",
                 "Environment=PYTHONPATH=/opt/linko-runtime/venv/lib/python3.12/site-packages"):
        assert line in unit.splitlines()
    assert not [c for c in fake() if c[:2] == ["systemctl", "restart"]]  # a new profile is started by the owner
    out = []
    assert instance_adapter.main(["add", "new", *args(tmp_path, repo, "--port", "9123")], out.append) == 2
    assert "use apply new" in out[-1]
    assert instance_adapter.main(["add", "nobody-here", *args(tmp_path, repo, "--port", "1")], out.append) == 2
    assert "does not exist" in out[-1]


def test_rendered_adapter_mounts_the_whole_checkout_and_masks_secrets(tmp_path, fake):
    repo = checkout(tmp_path)
    (repo / ".venv/lib/python3.12/site-packages").mkdir(parents=True)
    (repo / "link.env").symlink_to(repo / ".env")  # never mounted over: a symlink target is not ours
    data = tmp_path / "instance-data"
    data.mkdir()
    config = instance_adapter.Config(
        name="work", user=ME, owner=ME, checkout=str(repo), code=str(tmp_path / "opt/code"),
        state=str(tmp_path / "state"), data=str(data), port=1,
        runtime=[(str(repo / ".venv/lib/python3.12/site-packages"), str(tmp_path / "opt/site-packages"))],
        group=MY_GROUP)
    adapter = tmp_path / "adapter.py"
    adapter.write_text(instance_adapter.render(config))
    result = subprocess.run([sys.executable, str(adapter), "pre"], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    calls = [c for c in fake() if c[0] in ("mount", "umount")]
    code = str(tmp_path / "opt/code")
    assert calls[:2] == [["mount", "--bind", str(repo), code], ["mount", "-o", "remount,bind,ro,nosuid,nodev", code]]
    binds = {c[3]: c[2] for c in calls if c[1] == "--bind"}
    assert binds[code + "/.git"] == str(tmp_path / "state/.git")  # HEAD only, never the real .git
    assert binds[code + "/.env"] == "/dev/null" and binds[code + "/prod.env"] == "/dev/null"
    assert ["mount", "-t", "tmpfs", "-o", "ro,nosuid,nodev,noexec,size=4k,mode=0555", "tmpfs", code + "/secrets"] in calls
    assert code + "/link.env" not in binds and code + "/ai_manager" not in binds  # one mount covers every code path
    assert binds[str(tmp_path / "opt/site-packages")] == str(repo / ".venv/lib/python3.12/site-packages")
    assert (tmp_path / "state/.git/HEAD").read_text() == COMMIT + "\n"
    assert not any(c[0] == "setfacl" and str(repo) == c[-1] for c in fake())  # no per-file ACL on the checkout
    assert ["setfacl", "-m", f"u:{ME}:rwx,d:u:{ME}:rwx,d:u:{ME}:rwx"] == [c for c in fake() if c[0] == "setfacl"][-2][:3]


def test_adapter_template_masks_what_the_deploy_check_expects():
    """The root adapter keeps its own copy of the mask rules (it imports nothing from the checkout)."""
    namespace = {}
    source = instance_adapter.TEMPLATE.read_text()
    exec(compile(source.split("\nif __name__ == '__main__':")[0], "adapter", "exec"), namespace)
    assert tuple(namespace["MASK"]) == code_view.MASKED
    names = [".git", ".env", ".env.local", "prod.env", ".data", "secrets", ".linko-runtime", "ai_manager", ".venv",
             "README.md", "environment"]
    assert namespace["masked"](names) == code_view.masked(names)
    assert instance_adapter.BEGIN in source and instance_adapter.END in source


def acl_supported(path):
    try:
        os.getxattr(path, "system.posix_acl_access")
    except OSError as error:
        return error.errno == errno.ENODATA
    return True


def whole_adapter(tmp_path, repo, user=ME, group=MY_GROUP):
    path = tmp_path / "linko-work-instance"
    config = instance_adapter.Config(name="work", user=user, owner=ME, checkout=str(repo), code="/opt/linko-work/code",
                                     state="/var/lib/linko-work", data=str(tmp_path / "d"), port=1, group=group)
    path.write_text(instance_adapter.render(config))
    return path


def test_inspect_finds_and_repairs_an_unreadable_tracked_file(tmp_path):
    repo = checkout(tmp_path)
    if not acl_supported(repo):
        pytest.skip("no POSIX ACLs on the test filesystem")
    gid = os.getgid()
    adapter = whole_adapter(tmp_path, repo)
    for path in [repo, *repo.rglob("*")]:
        if ".git" not in path.parts:
            code_view.grant(path, gid)
    assert code_view.inspect(repo, "work", adapter).problems == []
    os.chmod(repo / "ai_manager" / "core.py", 0o600)  # what an editor's atomic write does: the mask drops to ---
    assert not code_view.group_can(repo / "ai_manager" / "core.py", gid)
    report = code_view.inspect(repo, "work", adapter)
    assert report.problems and "cannot read ai_manager/core.py" in report.problems[0]
    assert "instance_adapter.py apply work" in report.problems[0]
    assert code_view.inspect(repo, "work", adapter, repair=True).problems == []
    assert code_view.group_can(repo / "ai_manager" / "core.py", gid)
    assert os.stat(repo / "ai_manager" / "core.py").st_mode & 0o707 == 0o600  # owner and other untouched
    # secrets are never checked (nor granted): they are masked from the instance
    os.chmod(repo / ".env", 0o600)
    assert code_view.inspect(repo, "work", adapter, repair=True).problems == []
    assert not code_view.group_can(repo / ".env", gid)


def test_inspect_rejects_another_checkout_a_missing_group_or_a_non_member(tmp_path):
    repo = checkout(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    report = code_view.inspect(other, "work", whole_adapter(tmp_path, repo))
    assert report.problems and f"mounts {repo}, not {other}" in report.problems[0]
    report = code_view.inspect(repo, "work", whole_adapter(tmp_path, repo, group="linko-no-such-group"))
    assert "group linko-no-such-group does not exist" in report.problems[0]
    outsider = next((g for g in grp.getgrall() if ME not in g.gr_mem and g.gr_gid != os.getgid()), None)
    if outsider:
        report = code_view.inspect(repo, "work", whole_adapter(tmp_path, repo, group=outsider.gr_name))
        assert f"user {ME} is not in group {outsider.gr_name}" in report.problems[0]


def test_deploy_stops_on_a_wrong_whole_checkout_adapter_and_check_reports_it(tmp_path, fake):
    sys.path.insert(0, str(ROOT / "scripts"))
    import deploy
    repo = checkout(tmp_path)
    adapter = whole_adapter(tmp_path, tmp_path / "elsewhere")
    (tmp_path / "data" / "deploy").mkdir(parents=True)
    config = {"repo": str(repo), "data": str(tmp_path / "data"),
              "instances": [{"name": "work", "data": str(tmp_path / "work"), "url": "http://127.0.0.1:1",
                             "service": "linko-bot-manager@work.service", "adapter": str(adapter)}]}
    with pytest.raises(deploy.Stop) as stopped:
        deploy.Deploy(config).code_views()
    assert str(stopped.value).startswith(f"instance work {adapter} mounts {tmp_path / 'elsewhere'}, not {repo}")
    (tmp_path / "data" / "deploy" / "installed.json").write_text(json.dumps(config))
    out = []
    assert instance_adapter.main(["check", "--checkout", str(repo), "--data", str(tmp_path / "data")], out.append) == 1
    assert out[0].startswith("FAIL instance work")
    whole_adapter(tmp_path, repo)
    if acl_supported(repo):
        deploy.Deploy(config).code_views()  # repairs what this user owns, then goes on
        out = []
        assert instance_adapter.main(["check", "--checkout", str(repo), "--data", str(tmp_path / "data")],
                                     out.append) == 0
        assert out[0] == "OK   instance work: whole-checkout"


def test_release_commit_survives_a_code_directory_owned_by_another_user(tmp_path, monkeypatch):
    repo = checkout(tmp_path)
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    real = subprocess.run

    def foreign(argv, **kwargs):  # as the instance user sees the whole-checkout bind: owned by someone else
        kwargs["env"] = {**kwargs.get("env", {}), "GIT_TEST_ASSUME_DIFFERENT_OWNER": "1"}
        return real(argv, **kwargs)
    monkeypatch.setattr(registry.subprocess, "run", foreign)
    assert registry.git(repo, "rev-parse", "HEAD") == ""  # what plain git does there: dubious ownership
    assert registry.release_head(repo) == head
