"""Files delivery: worker harnesses with "delivery": "files" (Office, Humanizer) deliver output files, never merge or deploy.

Real Git repositories, the real core loop and chat delivery; the model is the scripted fake server of test_core.
"""
import asyncio
import hashlib
import json
from pathlib import Path

import pytest
from fake_codex import FakeServer
from test_core import git, run_next
from test_core import world as core_world
from test_harness import World, worker_manifest, write
from test_web import client as web_client
from test_web import sign_in

from ai_manager import delivery
from ai_manager import harness
from ai_manager.chat import Chat

world = core_world
client = web_client
SOURCE = "Chapter one.\nThe owner's original text.\n"


def counting_gate(world):
    calls = []
    original = world.core.gate

    def gate(root, base):
        calls.append(root)
        return original(root, base)
    world.core.gate = gate
    return calls


def with_source(world):
    (world.app / "docs").mkdir()
    (world.app / "docs" / "Book.md").write_text(SOURCE)
    git(world.app, "add", "-A")
    git(world.app, "commit", "-qm", "owner document")
    return git(world.app, "rev-parse", "main")


async def summarize(root, prompt):
    (Path(root) / "docs" / "Book summary.md").write_text("A short summary.\n")
    (Path(root) / "Notes summary.md").write_text("Notes from a chat attachment.\n")
    (Path(root) / ".linko-work").mkdir(exist_ok=True)
    (Path(root) / ".linko-work" / ".gitignore").write_text("*\n")
    (Path(root) / ".linko-work" / "chunk.txt").write_text("scratch\n")
    return "Wrote the summaries."


# ---- flag parsing ------------------------------------------------------------------------------------------------
@pytest.mark.smoke
def test_delivery_flag_is_parsed_and_only_office_and_humanizer_deliver_files(tmp_path):
    assert harness.builtin("office").delivers_files and harness.builtin("humanizer").delivers_files
    for code in ("coding", "zero-worker"):
        assert harness.builtin(code).delivery == "merge" and not harness.builtin(code).delivers_files
    assert harness.builtin("office").public()["delivery"] == "files"
    assert harness.builtin("coding").public()["delivery"] == "merge"
    assert "delivery" not in harness.builtin("linko").public()
    for name in ("office", "humanizer"):
        prompt = harness.builtin(name).instructions()
        assert "do not commit" in prompt and "Commit the" not in prompt and "merges; you produce" not in prompt
    world = World(tmp_path)
    write(tmp_path / "data/harnesses/docs", worker_manifest(delivery="files"))
    write(tmp_path / "data/harnesses/plain", worker_manifest(id="plain"))
    write(tmp_path / "data/harnesses/bad", worker_manifest(id="bad", delivery="email"))
    found, invalid = harness.catalog(world.db)
    assert found["docs"].delivers_files and found["plain"].delivery == "merge"
    assert [i["folder"] for i in invalid] == ["bad"] and "delivery must be 'merge'" in invalid[0]["error"]


# ---- the flow ----------------------------------------------------------------------------------------------------
@pytest.mark.smoke
@pytest.mark.asyncio
async def test_files_task_delivers_attachments_and_a_copy_and_never_merges_or_deploys(world):
    main = with_source(world)
    gate = counting_gate(world)
    chat = Chat(world.db, world.registry, FakeServer(world.settings))
    origin = chat.conversation(world.project["id"])["id"]
    task = world.tasks.add(world.project["id"], "summarize docs/Book.md", origin, harness="office")
    world.lab.actions = [summarize]
    done = await run_next(world)

    assert done["state"] == "done" and gate == []  # the code checks never ran
    assert done["blocker"] == "Delivered 2 files; 1 copied next to the original in the project folder"
    receipt = done["merge"]
    assert receipt["state"] == "delivered" and receipt["count"] == 2 and receipt["deploy"] is False
    assert git(world.app, "rev-parse", "main") == main  # nothing merged
    assert not world.pipeline.pending() and not (world.pipeline.root / "request.json").exists()
    assert not world.db.one("SELECT 1 FROM events WHERE task_id=? AND kind LIKE 'task.merge%'", (task["id"],))
    # The copy sits next to the original, which is unchanged; the work folder and the other output are not copied.
    by_path = {f["path"]: f for f in receipt["files"]}
    assert set(by_path) == {"docs/Book summary.md", "Notes summary.md"}
    assert by_path["docs/Book summary.md"]["copy"] == "docs/Book summary.md" and by_path["Notes summary.md"]["copy"] is None
    assert (world.app / "docs" / "Book summary.md").read_text() == "A short summary.\n"
    assert (world.app / "docs" / "Book.md").read_text() == SOURCE
    assert git(world.app, "status", "--porcelain") == '?? "docs/Book summary.md"'
    # The worker was told files mode and given no code self-check.
    prompt = world.lab.turns[0]
    assert prompt["delivery"]["mode"] == "files" and "self_check" not in prompt
    with pytest.raises(ValueError, match="delivers files"):
        world.tasks.request_merge(task["id"])

    # Each output is a stored chat attachment, attached to the manager's reply that reports the result.
    for entry in receipt["files"]:
        item = world.db.setting("attachment:" + entry["id"])
        stored = world.settings.data_dir / "chat-workspace" / "attachments" / item["stored_name"]
        assert stored.read_bytes() == (Path(done["workspace"]) / entry["path"]).read_bytes()
        assert item["sha256"] == hashlib.sha256(stored.read_bytes()).hexdigest() and item["name"] == entry["name"]
    await chat.deliver_results()
    await asyncio.gather(*list(chat.jobs.values()))
    reply = [m for m in chat.history(world.project["id"])["messages"] if m["role"] == "assistant"][-1]
    assert sorted(a["name"] for a in reply["attachments"]) == ["Book summary.md", "Notes summary.md"]
    assert {a["id"] for a in reply["attachments"]} == {f["id"] for f in receipt["files"]}
    notice = json.loads(world.db.one("SELECT content FROM messages WHERE role='worker'")["content"])
    assert notice["merge"]["summary"] == done["blocker"] and notice["deployment"].startswith("none: a files-delivery")


@pytest.mark.asyncio
async def test_delivery_never_overwrites_an_owner_file_and_a_retry_makes_no_duplicates(world):
    with_source(world)
    (world.app / "docs" / "Book summary.md").write_text("the owner's own summary\n")  # untracked, already there
    world.tasks.add(world.project["id"], "summarize", harness="office")
    world.lab.actions = [summarize]
    done = await run_next(world)
    copy = {f["path"]: f["copy"] for f in done["merge"]["files"]}["docs/Book summary.md"]
    assert copy == "docs/Book summary 2.md"
    assert (world.app / "docs" / "Book summary.md").read_text() == "the owner's own summary\n"
    assert (world.app / "docs" / "Book summary 2.md").read_text() == "A short summary.\n"
    task = world.tasks.get(done["id"])
    again = delivery.deliver(world.db, world.registry, task, world.project, done["workspace"])
    assert [f["copy"] for f in again["files"]] == [f["copy"] for f in done["merge"]["files"]]
    assert [f["id"] for f in again["files"]] == [f["id"] for f in done["merge"]["files"]]
    assert sorted(p.name for p in (world.app / "docs").iterdir()) == ["Book summary 2.md", "Book summary.md", "Book.md"]


@pytest.mark.asyncio
async def test_a_changed_original_is_a_repair_round_and_the_original_stays_untouched(world):
    with_source(world)

    async def overwrite(root, prompt):
        (Path(root) / "docs" / "Book.md").write_text("humanized in place\n")
        return "Done."

    async def fixed(root, prompt):
        assert prompt["repair"]["failed_checks"] == ["files"]
        git(root, "checkout", "--", "docs/Book.md")
        (Path(root) / "docs" / "Book humanized.md").write_text("humanized copy\n")
        return "Done."

    world.tasks.add(world.project["id"], "humanize", harness="humanizer")
    world.lab.actions = [overwrite, fixed]
    done = await run_next(world)
    assert done["state"] == "done" and done["rounds"] == 1
    assert "docs/Book.md is an original" in done["history"][0]["failures"][0]
    assert [f["path"] for f in done["merge"]["files"]] == ["docs/Book humanized.md"]
    assert (world.app / "docs" / "Book.md").read_text() == SOURCE
    assert (world.app / "docs" / "Book humanized.md").read_text() == "humanized copy\n"


def test_file_checks_catch_secrets_symlinks_and_too_many_files(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / "doc.md").write_text("original\n")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "base")
    base = git(root, "rev-parse", "HEAD")
    assert delivery.check(root, base)["ok"]
    (root / "out summary.md").write_text("token " + "ghp_" + "Q" * 36 + "\n")
    (root / "link.md").symlink_to(tmp_path)
    result = delivery.check(root, base)
    assert not result["ok"] and result["checks"] == ["files", "secrets"]
    assert any("out summary.md:1 looks like" in f for f in result["failures"])
    assert any("link.md is not a regular file" in f for f in result["failures"])
    assert "Q" * 36 not in result["output"]
    (root / "out summary.md").unlink()
    (root / "link.md").unlink()
    for n in range(delivery.MAX_FILES + 1):
        (root / f"part {n}.md").write_text("x\n")
    assert any(f"{delivery.MAX_FILES + 1} new files" in f for f in delivery.check(root, base)["failures"])


@pytest.mark.asyncio
async def test_coding_harness_flow_is_unchanged(world):
    gate = counting_gate(world)
    world.tasks.add(world.project["id"], "add a file", harness="coding")
    done = await run_next(world)
    assert gate and done["merge"]["state"] == "merged" and done["merge"]["deploy"] is True and done["blocker"] == ""
    assert (world.app / "change-1.txt").exists() and world.pipeline.pending()
    assert "self_check" in world.lab.turns[0] and "delivery" not in world.lab.turns[0]


def test_a_delivered_file_downloads_like_an_upload(client):
    c, app, settings = client
    sign_in(c, app, settings)
    task = {"id": "t" * 32, "project_id": None, "origin_conversation": None}
    identifier = delivery.attach(app.state.db, b"delivered bytes", "Report summary.md", task, "Report summary.md")
    assert delivery.attach(app.state.db, b"delivered bytes", "Report summary.md", task, "Report summary.md") == identifier
    response = c.get("/api/attachments/" + identifier)
    assert response.status_code == 200 and response.content == b"delivered bytes"
    assert "Report%20summary.md" in response.headers["content-disposition"] or \
        "Report summary.md" in response.headers["content-disposition"]
