"""Office worker harness (harnesses/office): picker entry, prompt rules, chunk planner, budget, resume, dash fix."""
import importlib.util
import json
import random
import subprocess
import sys
from pathlib import Path

import pytest
from test_followups import World

from ai_manager import harness

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "harnesses/office/tools/office.py"


@pytest.fixture
def office(tmp_path, monkeypatch):
    monkeypatch.setenv("OFFICE_ROOT", str(tmp_path))
    spec = importlib.util.spec_from_file_location("office_tool", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def book(path, paragraphs=3000, words=120, seed=7):
    """A large generated text (no big fixture files): about 500k tokens at the defaults."""
    rng = random.Random(seed)
    vocab = "river harbor lantern meadow orchard quarry saddle timber valley willow".split()
    path.write_text("\n\n".join(" ".join(rng.choice(vocab) for _ in range(words)) + "." for _ in range(paragraphs)))
    return path


# ---- harness -------------------------------------------------------------------------------------------------------
def test_office_is_a_builtin_worker_in_the_picker_with_its_tool_path(tmp_path):
    world = World(tmp_path)
    office = harness.builtin("office")
    assert office.role == "worker" and office.name == "Office" and office.source == "builtin"
    assert office.description.startswith("For office work: summaries, translations, extracts and writing")
    state = harness.state(world.db, world.project)
    assert "office" in [o["id"] for o in state["options"]["worker"]]
    assert "office" not in [o["id"] for o in state["options"]["manager"]]
    assert harness.save(world.db, world.project, {"worker": "office"}) is None
    assert harness.default_worker(world.db, world.project) == "office"
    assert {"id": "office", "name": "Office", "description": office.description} in \
        harness.context(world.db, world.project)["worker_options"]
    text = office.instructions()
    assert harness.HARNESS_DIR not in text and f"`python3 {TOOL}`" in text
    _, items = harness.rule_items(harness.rules_text("worker", "all"))
    for number in (1, 3, 10):  # the hard rules are always in
        assert items[number].rstrip("\n") in text
    assert items[4].split("\n", 1)[0] not in text  # office work adds no code tests


def test_prompt_has_the_chunking_budget_and_style_rules():
    text = harness.builtin("office").prompt
    for rule in ("Never `cat`, open or read a large document whole", "offer OCR as a separate step",
                 "under about 20k tokens", ".linko-work/<doc>/", "above about 10% of the limit", "chunk N/M",
                 "cheap model (Haiku)", "first try plus 2 retries", "budget cap", "Never overwrite",
                 "office.py dashfix", "keyword", "Resume"):
        assert rule in text, rule
    for rule in ("Never use the em dash (—) or the en dash (–)", "Avoid parenthetical asides", "„…“",
                 "no markdown, headings, bullet lists or emoji", "Vary sentence length", "not only X but also Y",
                 "ეს არ არის მხოლოდ X", "delve", "tapestry", "ultimately", "აღსანიშნავია", "უდავოდ",
                 "„ასევე“ at most once", "Great question", "Reread the final text once"):
        assert rule in text, rule
    body = text.replace("(—)", "").replace("(–)", "")
    assert "—" not in body and "–" not in body  # the prompt follows its own rule


def test_harness_dir_placeholder_is_replaced_for_external_harnesses(tmp_path):
    folder = tmp_path / "mine"
    folder.mkdir()
    (folder / "harness.json").write_text(json.dumps({"id": "mine", "name": "Mine", "role": "worker",
                                                     "description": "x", "prompt": "p.md"}))
    (folder / "p.md").write_text("Run {{HARNESS_DIR}}/tools/x.py\n")
    assert harness.load(folder).prompt == f"Run {folder.resolve()}/tools/x.py\n"


# ---- dash fix ------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("before,after", [
    ("Years 2020–2024 and pages 5 — 7.", "Years 2020-2024 and pages 5-7."),
    ("He paused — then he left.", "He paused, then he left."),
    ("He paused—then he left.", "He paused, then he left."),
    ("— Hello, she said.", "- Hello, she said."),
    ("  – გამარჯობა, თქვა მან.", "  - გამარჯობა, თქვა მან."),
    ("> — Quoted reply", "> - Quoted reply"),
    ("It ended —.", "It ended."),
    ("Wait —, no.", "Wait, no."),
    ("A long clause —", "A long clause,"),
    ("Use `a—b` and https://example.org/a–b here — fine.", "Use `a—b` and https://example.org/a–b here, fine."),
    ("See [the 2020–2024 report](https://x.org/r—1).", "See [the 2020–2024 report](https://x.org/r—1)."),
    ("Run `tool --flag -- file` with a well-known option -- yes", "Run `tool --flag -- file` with a well-known option -- yes"),
    ("A plain-hyphen line - stays.", "A plain-hyphen line - stays."),
])
def test_dash_line_cases(office, before, after):
    assert office.fix_line(before) == after


def test_dash_fix_skips_fenced_code_and_tables(office):
    text = ("Intro — one.\n```bash\nrun — x --flag\n```\n~~~\n2020–2024\n~~~\n| Range | 2020–2024 — x |\n|---|---|\n"
            "Outro – two.\r\n")
    assert office.fix_dashes(text) == ("Intro, one.\n```bash\nrun — x --flag\n```\n~~~\n2020–2024\n~~~\n"
                                       "| Range | 2020–2024 — x |\n|---|---|\nOutro, two.\r\n")


def test_dashfix_never_rewrites_an_original(office, tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, timeout=60)
    original = tmp_path / "Letter.md"
    original.write_text("Dear Nino — thanks.\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "Letter.md"], check=True, timeout=60)
    with pytest.raises(office.Stop, match="pass --out"):
        office.dashfix(original)
    assert original.read_text() == "Dear Nino — thanks.\n"
    out = office.output_path(original, "fixed")
    assert out.name == "Letter fixed.md"
    office.dashfix(original, out)
    assert out.read_text() == "Dear Nino, thanks.\n" and original.read_text() == "Dear Nino — thanks.\n"
    out.write_text("Again — here.\n")
    office.dashfix(out)  # a registered output may be fixed in place, even once committed
    assert out.read_text() == "Again, here.\n"
    untracked = tmp_path / "new draft.md"
    untracked.write_text("New – draft.\n")
    office.dashfix(untracked)
    assert untracked.read_text() == "New, draft.\n"
    with pytest.raises(office.Stop, match="already exists"):
        office.dashfix(untracked, original)


def test_outpath_never_reuses_an_existing_name(office, tmp_path):
    source = tmp_path / "Book.pdf"
    source.write_text("x")
    (tmp_path / "Book summary.md").write_text("owner's own file")
    assert office.output_path(source, "summary").name == "Book summary 2.md"
    with pytest.raises(office.Stop, match="original"):
        office.guard_new_output(source, source)
    with pytest.raises(office.Stop, match="not an output"):
        office.guard_new_output(source, tmp_path / "Book summary.md")


# ---- planner, estimate, budget, resume, loop guard -----------------------------------------------------------------
def test_measure_and_small_documents_need_no_chunks(office, tmp_path):
    small = tmp_path / "memo.txt"
    small.write_text("Short memo. " * 200)
    info = office.measure_text(*office.extract(small))
    assert info["tier"] == "small" and 500 < info["tokens"] < 1000
    state, lines = office.plan(small)
    assert len(state["chunks"]) == 1 and state["confirmed"] and any("small document" in line for line in lines)
    georgian = "ქართული ტექსტი " * 100
    assert office.estimate_tokens(georgian) > 2 * office.estimate_tokens("a" * len(georgian))


def test_scanned_pdf_is_reported_not_ocred(office):
    assert office.measure_text("\f\f  \f", 4)["scanned"]
    assert not office.measure_text("words " * 400, 2)["scanned"]


def test_large_book_plan_estimate_and_confirmation(office, tmp_path):
    source = book(tmp_path / "Big book.txt")
    state, lines = office.plan(source)
    chunks = state["chunks"]
    assert 60 < len(chunks) < 120 and all(c["tokens"] <= office.CHUNK_TOKENS for c in chunks)
    work = office.work_dir(source)
    assert work.parent == tmp_path / ".linko-work" and (work.parent / ".gitignore").read_text().endswith("*\n")
    joined = "\n\n".join((work / "chunks" / f"{c['id']:04d}.txt").read_text() for c in chunks)
    assert joined.split() == source.read_text().split()  # nothing lost or duplicated
    assert lines[1].startswith(f"estimate: {len(chunks)} chunk(s), about ") and "Haiku" in lines[1]
    assert state["estimate"]["needs_confirmation"] and lines[-1].startswith("STOP:")
    with pytest.raises(office.Stop, match="waiting for the owner") as stop:
        office.handout(source)
    assert stop.value.code == office.STOP
    # Without sub-agents the same job costs about four times as much in the estimate.
    direct = office.estimate([c["tokens"] for c in chunks], "summary", False)["percent"]
    assert direct > 3 * state["estimate"]["percent"]
    # The 2026-11-20 case: a ~190-page book (~130k tokens) summarized with cheap chunks stays well under 10%.
    small_book = office.estimate([6000] * 22, "summary", True)
    assert not small_book["needs_confirmation"] and small_book["percent"] < 5


def test_chunk_loop_progress_resume_and_combine(office, tmp_path):
    source = book(tmp_path / "Novel.txt", paragraphs=500)
    state, _ = office.plan(source, chunk_tokens=3000)
    total = len(state["chunks"])
    assert state["confirmed"] and total > 4
    work = office.work_dir(source)
    first = office.handout(source, batch=2)
    assert first[0].startswith(f"chunk 1/{total} ") and first[1].startswith(f"chunk 2/{total} ")
    for n in (1, 2):
        (work / "parts" / f"{n:04d}.md").write_text(f"Summary of part {n}.\n")
        office.mark_done(source, n)
    # A resumed task (a retry or continue) keeps the saved chunks and results instead of rereading.
    (work / "source.txt").unlink()
    resumed, lines = office.plan(source, chunk_tokens=3000)
    assert lines == [f"resuming: 2/{total} chunks done, saved work in {work}"] and not (work / "source.txt").exists()
    assert office.handout(source)[0].startswith(f"chunk 3/{total} ")
    with pytest.raises(office.Stop, match="identical to chunk 1"):
        (work / "parts" / "0003.md").write_text("Summary of part 1.\n")
        office.mark_done(source, 3)
    with pytest.raises(office.Stop, match="finish them first"):
        office.combine(source)
    for n in range(3, total + 1):
        if n > 3:
            office.handout(source)
        (work / "parts" / f"{n:04d}.md").write_text(f"Summary of part {n}.\n")
        office.mark_done(source, n)
    assert office.handout(source)[0].startswith(f"all {total} chunks done")
    lines = office.combine(source)
    final = work / "combine" / "final-input.md"
    assert str(final) in lines[0] and final.read_text().count("[chunk ") == total
    # Hierarchical: a tiny budget asks for group summaries first, then builds the final input from them.
    lines = office.combine(source, budget=12)
    assert lines[0].startswith("hierarchical combine level 1") and len(lines) > 2
    for group in sorted((work / "combine" / "level1").glob("group-*[0-9].md")):
        group.with_name(group.stem + ".summary.md").write_text(f"Group {group.stem}.\n")
    assert office.combine(source, budget=10 ** 6)[0].startswith("final pass")
    out = office.output_path(source, "translation")
    office.assemble(source, out)
    assert out.read_text().startswith("Summary of part 1.\n\nSummary of part 2.")
    hits = office.search(source, "lantern")
    assert hits[0].startswith("chunk ") and len(hits) <= office.QA_TOP
    assert office.search(source, "zeppelin") == ["no chunk matches; try other keywords or synonyms"]


def test_budget_cap_saves_progress_and_confirm_raises_it(office, tmp_path, monkeypatch):
    source = book(tmp_path / "Report.txt", paragraphs=400)
    state, _ = office.plan(source, chunk_tokens=3000)
    work = office.work_dir(source)
    two = sum(c["units"] for c in state["chunks"][:2])
    state["cap_percent"] = 100 * (two + state["chunks"][2]["units"] / 2) / office.LIMIT_UNITS  # room for two chunks
    office.save_state(work, state)
    for n in (1, 2):
        office.handout(source)
        (work / "parts" / f"{n:04d}.md").write_text(f"Part {n}.\n")
        office.mark_done(source, n)
    with pytest.raises(office.Stop, match="budget cap .* reached at 2/") as stop:
        office.handout(source)
    assert stop.value.code == office.STOP and office.load_state(work)["status"] == "capped"
    office.confirm(source, cap=100)
    assert office.handout(source)[0].startswith("chunk 3/")


def test_loop_guard_stops_after_two_retries(office, tmp_path):
    source = book(tmp_path / "Thread.txt", paragraphs=200)
    office.plan(source, chunk_tokens=3000)
    for attempt in (1, 2, 3):
        assert f"attempt {attempt}/3" in office.handout(source)[0]
        office.mark_failed(source, 1, "model returned nothing")
    with pytest.raises(office.Stop, match="loop guard: chunk 1/.* failed 3 times") as stop:
        office.handout(source)
    assert stop.value.code == office.LOOP
    with pytest.raises(office.Stop, match="loop guard"):  # stays stopped
        office.handout(source)


def test_cli_prints_progress_and_exit_codes(tmp_path):
    source = book(tmp_path / "Doc.txt", paragraphs=200)
    env = {"OFFICE_ROOT": str(tmp_path), "PATH": "/usr/bin:/bin"}

    def run(*args):
        return subprocess.run([sys.executable, "-I", str(TOOL), *args], capture_output=True, text=True, env=env,
                              timeout=60)

    measured = run("measure", str(source))
    assert measured.returncode == 0 and json.loads(measured.stdout)["tier"] == "large"
    planned = run("plan", str(source), "--chunk-tokens", "3000")
    assert planned.returncode == 0 and "estimate:" in planned.stdout
    handed = run("next", str(source))
    assert handed.returncode == 0 and handed.stdout.startswith("chunk 1/")
    assert run("done", str(source), "1").returncode == 2  # no output written yet
    assert run("dashfix", str(source)).returncode == 2  # not a git repo: an unknown file is treated as an original
    fixed = Path(run("outpath", str(source), "fixed").stdout.strip())
    assert fixed.name == "Doc fixed.md" and not fixed.exists()
    fixed.write_text("A — b.\n")
    assert run("dashfix", str(fixed)).returncode == 0 and fixed.read_text() == "A, b.\n"
