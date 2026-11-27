"""Humanizer: the level 2 script (rates, exclusions, one error per word, deletion-only punctuation, new files, Georgian,
.docx), the Writing settings (per profile, measured samples) and their use in Office and Humanizer task prompts."""
import hashlib
import random
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_followups import World
from test_restricted import instance, sign_in

from ai_manager import harness, humanize, writing

ROOT = Path(__file__).resolve().parents[1]
tool = humanize.tool
EN = "river harbor lantern meadow orchard quarry saddle timber valley willow morning letter garden window".split()
KA = "მდინარე ნავსადგური ფანარი მდელო ბაღი კარიერი უნაგირი ტყე ხეობა ტირიფი დილა წერილი ფანჯარა".split()


def prose(words, paragraphs=400, sentences=6, seed=11):
    """Generated paragraphs of plain lowercase words with commas, colons and questions (no fixture files)."""
    rng = random.Random(seed)
    out = []
    for _ in range(paragraphs):
        parts = []
        for _ in range(sentences):
            sentence = " ".join(rng.choice(words) for _ in range(rng.randint(6, 14)))
            sentence = sentence.replace(" ", ", ", 1) if rng.random() < 0.7 else sentence
            if rng.random() < 0.2:
                sentence = sentence.replace(" ", ": ", 1)
            parts.append(sentence[0].upper() + sentence[1:] + rng.choice(".....?!"))
        out.append(" ".join(parts))
    return "\n\n".join(out) + "\n"


def letters(chunk):
    return re.sub(r"[^\w]", "", chunk)


def one_edit(a, b):
    """b is a with exactly one insert, delete or adjacent swap."""
    if len(b) == len(a) + 1:
        return any(b[:i] + b[i + 1:] == a for i in range(len(b)))
    if len(b) == len(a) - 1:
        return any(a[:i] + a[i + 1:] == b for i in range(len(a)))
    if len(a) == len(b):
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        return len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]]
    return False


def changes(before, after):
    """Whitespace never changes, so the chunks line up one to one."""
    a, b = before.split(), after.split()
    assert len(a) == len(b)
    return [(x, y) for x, y in zip(a, b) if x != y]


def check_edits(before, after):
    typos = drops = 0
    for old, new in changes(before, after):
        if letters(old) == letters(new):
            # punctuation only: the new chunk is the old one with marks removed, never added
            removed = list(old)
            for c in new:
                removed.remove(c)
            assert removed and all(c in tool.PUNCT for c in removed), (old, new)
            assert re.sub(f"[{re.escape(tool.PUNCT)}]", "", old) == re.sub(f"[{re.escape(tool.PUNCT)}]", "", new)
            drops += len(removed)
        else:
            assert one_edit(letters(old), letters(new)), (old, new)
            typos += 1
    return typos, drops


# ---- rates and edit shape ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("words", [EN, KA], ids=["english", "georgian"])
def test_rates_are_respected_and_every_edit_is_one_typo_or_a_deleted_mark(words):
    text = prose(words)
    for typos, punct in ((0.2, 2.0), (1.0, 5.0)):
        result, edits = humanize.humanize_text(text, typos, punct, seed=5)
        found_typos, found_drops = check_edits(text, result)
        assert found_typos == len(edits["words"]) and found_drops == len(edits["marks"])
        assert abs(len(edits["words"]) - edits["eligible_words"] * typos / 100) <= 1
        assert abs(len(edits["marks"]) - edits["eligible_marks"] * punct / 100) <= 1
        assert edits["eligible_words"] > 10000 and edits["eligible_marks"] > 1000
        assert len({start for start, *_ in edits["words"]}) == len(edits["words"])  # at most one error per word


def test_zero_rates_change_nothing_and_the_seed_reproduces():
    text = prose(EN, paragraphs=50)
    assert humanize.humanize_text(text, 0, 0, seed=1)[0] == text
    assert humanize.humanize_text(text, 2, 10, seed=9)[0] == humanize.humanize_text(text, 2, 10, seed=9)[0]
    assert humanize.humanize_text(text, 2, 10, seed=9)[0] != humanize.humanize_text(text, 2, 10, seed=10)[0]
    with pytest.raises(ValueError, match="typo rate"):
        humanize.humanize_text(text, 11, 2)
    with pytest.raises(ValueError, match="punctuation rate"):
        humanize.humanize_text(text, 0.2, -1)


def test_paragraph_and_line_final_marks_are_kept():
    text = prose(EN, paragraphs=300, sentences=3)
    result, _ = humanize.humanize_text(text, 0, 50, seed=3)
    old, new = text.split("\n\n"), result.split("\n\n")
    assert len(old) == len(new)
    assert all(a.rstrip()[-1] == b.rstrip()[-1] and b.rstrip()[-1] in ".?!" for a, b in zip(old, new))
    lines = "Short line one, with a comma.\nSecond line here, and more:\nthird line, done.\n"
    for seed in range(30):
        out, _ = humanize.humanize_text(lines * 20, 0, 50, seed=seed)
        assert [line[-1] for line in out.splitlines()] == [line[-1] for line in (lines * 20).splitlines()]


def test_georgian_typos_use_georgian_keyboard_neighbours():
    text = prose(KA, paragraphs=100)
    result, edits = humanize.humanize_text(text, 10, 0, seed=4)
    assert len(edits["words"]) > 100
    inserted = 0
    for start, end, new, kind in edits["words"]:
        assert all(tool.is_georgian(c) for c in new)
        if kind == "insert":
            old = text[start:end]
            assert any(new[:i] + new[i + 1:] == old and new[i] in tool.neighbours(new[i - 1])
                       for i in range(1, len(new))), (old, new)
            inserted += 1
    assert inserted
    assert set(tool.neighbours("ა")) == {"ქ", "წ", "ს", "ზ"}  # a: q w s z on the standard layout
    assert set(tool.neighbours("g")) >= {"f", "h", "t", "y", "v", "b"}


# ---- exclusions ----------------------------------------------------------------------------------------------------
PROTECTED_SAMPLE = """Intro paragraph with ordinary words.

Meeting with Natalia Beridze and Giorgi in Tbilisi went well, everyone agreed.
Call 555-0199 on 12.03.2026 or pay 1,250.50 GEL by v2 of the plan, item3 included.
See https://example.com/some/path, www.example.org, and write to someone@example.com today.
Use `inline, code: here` and the file config.yaml or src/app_main.py please.
She said "never, ever change this: quoted words" and „ქართული, ციტატა: აქ“ then left.
We go on if we do so as it is.

```
fenced code, with commas: and words.
more fenced lines here.
```

| column one, words | column two: words |
|---|---|
| table cell, text | other cell. text |
"""


KEPT = ("Natalia Beridze", "Giorgi", "Tbilisi", "GEL", "555-0199", "12.03.2026", "1,250.50", "v2", "item3",
        "https://example.com/some/path,", "www.example.org,", "someone@example.com", "`inline, code: here`",
        "config.yaml", "src/app_main.py", '"never, ever change this: quoted words"', "„ქართული, ციტატა: აქ“",
        "```\nfenced code, with commas: and words.\nmore fenced lines here.\n```",
        "| column one, words | column two: words |\n|---|---|\n| table cell, text | other cell. text |",
        "We go on if we do so as it is.")


def test_never_touches_digits_names_urls_code_quotes_tables_or_short_words():
    text = PROTECTED_SAMPLE * 30
    changed = 0
    for seed in range(40):
        result, edits = humanize.humanize_text(text, 10, 50, seed=seed)
        for piece in KEPT:
            assert result.count(piece) == text.count(piece) == 30, (seed, piece)
        for start, end, new, _ in edits["words"]:
            word = text[start:end]
            assert len(word) >= 3 and word.islower() and not any(c.isdigit() for c in word), word
        changed += len(edits["words"]) + len(edits["marks"])
        check_edits(text, result)
    assert changed  # the ordinary words around them do change


def test_mask_covers_each_kind():
    mask = tool.protected_mask(PROTECTED_SAMPLE)
    for piece in ("https://example.com/some/path", "someone@example.com", "`inline, code: here`",
                  '"never, ever change this: quoted words"', "„ქართული, ციტატა: აქ“", "fenced code, with commas",
                  "| table cell, text |", "1,250.50", "12.03.2026", "config.yaml"):
        start = PROTECTED_SAMPLE.index(piece)
        assert all(mask[start:start + len(piece)]), piece


# ---- files ---------------------------------------------------------------------------------------------------------
def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_file_goes_to_a_new_file_and_the_original_is_unchanged(tmp_path):
    source = tmp_path / "Letter.md"
    source.write_text(prose(EN, paragraphs=60), encoding="utf-8")
    before = digest(source)
    first, _ = humanize.humanize_file(source, 2, 10, seed=1)
    second, _ = humanize.humanize_file(source, 2, 10, seed=1)
    assert first.name == "Letter humanized.md" and second.name == "Letter humanized 2.md"
    assert digest(source) == before and first.read_text() == second.read_text() != source.read_text()
    for out in (source, first):
        with pytest.raises(ValueError):
            humanize.humanize_file(source, out=out)
    assert digest(source) == before and digest(first) == digest(second)
    (tmp_path / "x.pdf").write_bytes(b"%PDF")
    with pytest.raises(ValueError, match="unsupported"):
        humanize.humanize_file(tmp_path / "x.pdf")


def test_cli_module_writes_a_new_file(tmp_path):
    source = tmp_path / "note.txt"
    source.write_text(prose(KA, paragraphs=40), encoding="utf-8")
    before = digest(source)
    done = subprocess.run([sys.executable, "-m", "ai_manager.humanize", "humanize", str(source), "--typos", "1",
                           "--punct", "5", "--seed", "7"], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert "wrote" in done.stdout and "seed 7" in done.stdout
    out = tmp_path / "note humanized.txt"
    assert out.exists() and digest(source) == before
    check_edits(source.read_text(), out.read_text())
    done = subprocess.run([sys.executable, str(tool.__file__), "measure", str(source)], capture_output=True, text=True,
                          timeout=60)
    assert done.returncode == 0 and '"avg_sentence_words"' in done.stdout


def make_docx(path, paragraphs, table):
    body = "".join(f'<w:p><w:r><w:t>{a}</w:t></w:r><w:r><w:rPr><w:b/></w:rPr><w:t xml:space="preserve">{b}</w:t></w:r></w:p>'
                   for a, b in paragraphs)
    body += f"<w:tbl><w:tr><w:tc><w:p><w:r><w:t>{table}</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
    xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats'
           f'.org/wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>')
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", xml)


def docx_text(path):
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode()
    return xml, re.findall(r"<w:t[^>]*>(.*?)</w:t>", xml)


def test_docx_text_is_humanized_in_a_new_package(tmp_path):
    rng = random.Random(2)
    paragraphs = []
    for _ in range(200):
        words = [rng.choice(EN) for _ in range(20)]
        paragraphs.append((" ".join(words[:10]) + ",", " " + " ".join(words[10:]) + ", and done."))
    source = tmp_path / "Report.docx"
    make_docx(source, paragraphs, "table words, kept as they are, always.")
    before = digest(source)
    out, edits = humanize.humanize_file(source, 3, 20, seed=8)
    assert out.name == "Report humanized.docx" and digest(source) == before
    assert len(edits["words"]) > 50 and len(edits["marks"]) > 10
    old_xml, old_runs = docx_text(source)
    new_xml, new_runs = docx_text(out)
    assert len(old_runs) == len(new_runs) and new_runs[-1] == old_runs[-1]  # the table is untouched
    assert re.sub(r">[^<]*<", "><", old_xml) == re.sub(r">[^<]*<", "><", new_xml)  # every tag kept
    check_edits(" ".join(old_runs), " ".join(new_runs))
    assert all(run.endswith(".") for run in new_runs[1:-1:2])  # paragraph-final periods kept
    with zipfile.ZipFile(out) as z:
        assert z.namelist() == ["[Content_Types].xml", "word/document.xml"]


# ---- samples, settings and prompts ---------------------------------------------------------------------------------
EN_SAMPLE = ("I wrote this on the train. It was late and the carriage was almost empty, so I had time to think.\n\n"
             "Was it worth it? Probably. The plan had one weak spot: the budget. We fixed it - slowly.")


def test_measure_reports_the_sample_statistics():
    stats = humanize.measure(EN_SAMPLE)
    assert stats["sentences"] == 6 and stats["paragraphs"] == 2 and stats["words"] == 38
    assert stats["avg_sentence_words"] == round(38 / 6, 1) and stats["commas_per_sentence"] == round(1 / 6, 2)
    assert stats["avg_paragraph_sentences"] == 3.0
    marks = stats["per_1000_words"]
    assert marks["?"] == round(1000 / 38, 1) and marks[":"] == round(1000 / 38, 1) and marks["-"] == round(1000 / 38, 1)
    assert marks["!"] == 0 and "commas per sentence" in humanize.describe(stats)


def test_settings_persist_per_language_and_feed_office_and_humanizer_prompts(tmp_path):
    world = World(tmp_path)
    assert writing.load(world.db)["typo_rate"] == 0.2 and writing.load(world.db)["punct_rate"] == 2.0
    ka = "დილით ადრე გავედით. გზა მშვიდი იყო, ცა კი მოწმენდილი.\n\nრა დროს ვიყავით? არ ვიცი."
    writing.save(world.db, {"samples": {"en": EN_SAMPLE, "ka": ka}, "typo_rate": 0.5, "punct_rate": 3})
    world.boot()  # persisted in the database, not in memory
    saved = writing.load(world.db)
    assert saved["samples"] == {"en": EN_SAMPLE, "ka": ka} and saved["typo_rate"] == 0.5 and saved["punct_rate"] == 3
    assert saved["stats"]["en"] == humanize.measure(EN_SAMPLE) and saved["stats"]["ka"]["sentences"] == 4
    writing.save(world.db, {"samples": {"ka": ""}})  # one language at a time; clearing drops its stats
    saved = writing.load(world.db)
    assert saved["samples"]["en"] == EN_SAMPLE and saved["samples"]["ka"] == "" and saved["stats"]["ka"] is None
    for bad in ({"typo_rate": 11}, {"punct_rate": -1}, {"samples": {"de": "x"}}, {"samples": {"en": "x" * 6001}}):
        with pytest.raises(ValueError):
            writing.save(world.db, bad)
    core = world.scheduler.core
    project = world.registry.get(world.project)
    for harness_id in ("office", "humanizer", "coding"):
        task = world.tasks.get(world.tasks.add(world.project, f"Job for {harness_id}", harness=harness_id)["id"])
        prompt = core.prompt(task, project, project["path"], "work")
        if harness_id == "coding":
            assert "writing_style" not in prompt
            continue
        style = prompt["writing_style"]
        assert style["samples"] == {"en": {"language": "English", "sample": EN_SAMPLE,
                                           "stats": humanize.describe(humanize.measure(EN_SAMPLE))}}
        assert "No semicolons" in style["punctuation"] and "em dash" in style["punctuation"]
        assert style["humanizer_defaults"] == {"typos_percent": 0.5, "punct_percent": 3.0}


def test_humanizer_is_a_builtin_worker_with_its_tool():
    found = harness.builtin("humanizer")
    assert found.role == "worker" and found.name == "Humanizer"
    text = found.instructions()
    assert f"python3 {tool.__file__}" in text and harness.HARNESS_DIR not in text
    for rule in ("writing_style", "--typos", "--punct", "NEW file", "Never overwrite", "no semicolons"):
        assert rule in text, rule
    assert "writing_style" in harness.builtin("office").prompt


@pytest.mark.parametrize("restricted", [False, True], ids=["main", "instance"])
def test_writing_settings_api_saves_on_main_and_on_an_instance(tmp_path, restricted):
    settings, app = instance(tmp_path, restricted=restricted)
    with TestClient(app, base_url="http://localhost") as browser:
        sign_in(browser, settings)
        data = browser.get("/api/writing/settings").json()
        assert data["samples"] == {"en": "", "ka": ""} and data["typo_rate"] == 0.2 and data["punct_rate"] == 2.0
        saved = browser.put("/api/writing/settings", json={"samples": {"en": EN_SAMPLE}, "typo_rate": 0.4,
                                                           "punct_rate": 1.5})
        assert saved.status_code == 200, saved.text
        assert saved.json()["stats"]["en"]["sentences"] == 6
        again = browser.get("/api/writing/settings").json()
        assert again["samples"]["en"] == EN_SAMPLE and again["typo_rate"] == 0.4 and again["punct_rate"] == 1.5
        assert browser.put("/api/writing/settings", json={"typo_rate": 50}).status_code == 409
        assert browser.put("/api/writing/settings", json={"samples": {"en": "x" * 6001}}).status_code == 422
        browser.headers.pop("X-CSRF-Token")
        browser.cookies.clear()
        assert browser.get("/api/writing/settings").status_code == 401
