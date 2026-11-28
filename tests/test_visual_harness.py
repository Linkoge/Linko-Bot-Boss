"""Visual harness: the desktop capture helper (geometry and argument building, no real X) and the screenshot evidence
that rides on a merged task's result."""
import importlib.util
import json
import subprocess
import types
from pathlib import Path

import pytest

from ai_manager import delivery, harness

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("visual_tool", ROOT / "harnesses" / "visual" / "tools" / "visual.py")
visual = importlib.util.module_from_spec(spec)
spec.loader.exec_module(visual)


def test_visual_is_a_coding_style_merge_worker_with_a_short_description():
    found = harness.builtin("visual")
    assert found.role == "worker" and not found.delivers_files and found.rules == "all"
    assert "screenshots" in found.description and len(found.description) < 200
    text = found.instructions()
    assert "visual.py" in text and "{{HARNESS_DIR}}" not in text
    assert (ROOT / "harnesses" / "visual" / "tools" / "visual.py").is_file()
    assert harness.builtin("coding").prompt in text  # the Coding prompt and rules are kept


def test_geometry_parsing_clamping_and_strip():
    assert visual.parse_xdotool("WINDOW=5\nX=10\nY=20\nWIDTH=300\nHEIGHT=40\nSCREEN=0\n") == (10, 20, 300, 40)
    listing = "0x01 0 0 0 1920 30 host panel\n0x02 0 100 200 640 480 host Terminal\n"
    assert visual.parse_wmctrl(listing, name="terminal") == (100, 200, 640, 480)
    assert visual.parse_wmctrl(listing, wid="0x01") == (0, 0, 1920, 30)
    assert visual.clamp((-10, 1000, 500, 200), (1920, 1080)) == (0, 1000, 490, 80)
    assert visual.strip((1920, 1080)) == (0, 1016, 1920, 64)
    assert visual.parse_rect("1,2,3,4") == (1, 2, 3, 4)
    for bad in (lambda: visual.parse_xdotool("nothing"), lambda: visual.parse_wmctrl(listing, name="zzz"),
                lambda: visual.clamp((2000, 0, 10, 10), (1920, 1080)), lambda: visual.parse_rect("1,2")):
        with pytest.raises(visual.Stop):
            bad()


def test_grab_commands_per_tool():
    geometry = (10, 20, 300, 40)
    assert visual.grab_command("import", geometry, "a.png") == ["import", "-window", "root", "-crop", "300x40+10+20", "+repage", "a.png"]
    assert visual.grab_command("maim", geometry, "a.png") == ["maim", "-g", "300x40+10+20", "a.png"]
    assert visual.grab_command("scrot", geometry, "a.png") == ["scrot", "-o", "-a", "10,20,300,40", "a.png"]
    with pytest.raises(visual.Stop):
        visual.grab_command("nope", geometry, "a.png")


def test_capture_without_any_tool_is_a_clear_error_and_dry_run_builds_the_command(capsys):
    args = types.SimpleNamespace(out="x.png", display=":9", window=None, id=None, strip="0,980,1920,100", dry_run=True)
    with pytest.raises(visual.Stop, match="no screenshot tool"):
        visual.capture(args, which=lambda name: None)
    visual.capture(args, which=lambda name: "/usr/bin/" + name if name == "maim" else None)
    assert capsys.readouterr().out.strip() == "maim -g 1920x100+0+980 x.png"
    assert visual.main(["capture", "--out", "x.png", "--strip", "bad", "--dry-run"]) == 2


def test_evidence_add_copies_images_and_caps_at_twelve(tmp_path):
    folder = tmp_path / "evidence"
    folder.mkdir()
    shots = []
    for number in range(13):
        shots.append(tmp_path / f"s{number:02}.png")
        shots[-1].write_bytes(b"png")
    assert len(visual.evidence_add([str(p) for p in shots[:12]], folder)) == 12
    with pytest.raises(visual.Stop, match="cap"):
        visual.evidence_add([str(shots[12])], folder)
    (tmp_path / "note.txt").write_text("x")
    with pytest.raises(visual.Stop):
        visual.evidence_add([str(tmp_path / "note.txt")], folder)


def test_evidence_becomes_chat_attachment_references(tmp_path):
    from ai_manager.db import Database
    db = Database(tmp_path / "data" / "app.sqlite")
    root = tmp_path / "ws"
    (root / delivery.EVIDENCE_DIR).mkdir(parents=True)
    (root / delivery.EVIDENCE_DIR / "top.png").write_bytes(b"\x89PNG-top")
    (root / delivery.EVIDENCE_DIR / "notes.txt").write_text("skipped")
    task = {"id": "t" * 32, "project_id": "p", "origin_conversation": None}
    shots = delivery.collect_evidence(db, task, root)
    assert [s["name"] for s in shots] == ["top.png"]
    refs = delivery.references({"state": "merged", "evidence": shots})
    assert [r["name"] for r in refs] == ["top.png"] and refs[0]["mime"] == "image/png"
    assert db.setting("attachment:" + shots[0]["id"])["task_id"] == task["id"]
    assert delivery.references({"state": "merged"}) == [] and delivery.collect_evidence(db, task, tmp_path / "none") == []
    json.dumps(refs)
