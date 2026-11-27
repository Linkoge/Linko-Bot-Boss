#!/usr/bin/env python3
"""Office harness helper: measure, chunk, budget, resume and finish large-document jobs without any AI calls.

Standard library only (it runs with whatever python3 the worker has). Every command works on a work folder under the
task workspace, ``<root>/.linko-work/<doc>/``, which is git-ignored working memory: a retry or a resumed task continues
from the saved chunks and partial results and never rereads the whole document.

  office.py measure SOURCE                      pages, characters, estimated tokens, scanned-PDF check
  office.py plan SOURCE [--job J] [--no-subagents] [--chunk-tokens N] [--fresh]
                                                extract, split into chunks, print the cost estimate
  office.py confirm SOURCE [--cap PCT]          record the owner's confirmation of a large estimate
  office.py next SOURCE [--batch K]             hand out the next chunk(s): "chunk N/M", input and output paths
  office.py done SOURCE ID                      mark a chunk done once its output file is written
  office.py fail SOURCE ID [--reason R]         record a failed attempt (loop guard: at most 2 retries)
  office.py status SOURCE                       progress and spent budget
  office.py search SOURCE QUERY [--top N]       keyword search over chunks (Q&A reads only these)
  office.py combine SOURCE [--budget N]         build the final-pass input, hierarchical if needed
  office.py assemble SOURCE OUT                 join chunk outputs in order (chunk-by-chunk translation)
  office.py outpath SOURCE KIND [--ext .md] [--dir DIR] [--name NAME]
                                                a NEW output path next to the source ("<name> summary.md"); --dir
                                                puts it in DIR instead (a source outside the workspace), --name
                                                takes the stem from the original file name (a stored attachment)
  office.py dashfix FILE [--out OUT]            replace em/en dashes in a produced output, never in an original

Exit codes: 0 ok, 2 error, 3 stop for owner confirmation or budget cap (progress is saved), 4 loop guard stop.
"""

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

SMALL_TOKENS = 20000          # below this a document is handled directly, without chunks
CHUNK_TOKENS = 6000           # default chunk size for the cheap model
PROMPT_OVERHEAD = 2000        # instructions and framing sent with every chunk
OUTPUT_WEIGHT = 5             # output tokens cost about five input tokens
CHEAP_WEIGHT = 0.25           # a cheap-model (Haiku) token against a strong-model token
# Rough strong-model input-equivalent units in one 5-hour usage window. Only a planning figure: override with
# OFFICE_LIMIT_UNITS when the real plan is known.
LIMIT_UNITS = int(os.getenv("OFFICE_LIMIT_UNITS", "3000000"))
CONFIRM_PERCENT = float(os.getenv("OFFICE_CONFIRM_PERCENT", "10"))
CAP_PERCENT = float(os.getenv("OFFICE_CAP_PERCENT", "20"))
MAX_ATTEMPTS = 3              # the first try plus at most 2 retries
COMBINE_BUDGET = 12000        # tokens one final pass may read
QA_TOP = 5
JOBS = {  # output tokens per input token, and how chunk outputs are finished
    "summary": (0.12, "combine"), "detailed": (0.25, "combine"), "chapters": (0.2, "combine"),
    "keypoints": (0.1, "combine"), "qa": (0.1, "search"), "translate": (1.15, "assemble"),
    "extract": (0.3, "assemble"),
}
OK, ERROR, STOP, LOOP = 0, 2, 3, 4


class Stop(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


# ---- measuring ---------------------------------------------------------------------------------------------------
def estimate_tokens(text):
    """Latin text is about 4 characters a token; Georgian and other non-ASCII scripts tokenize far worse."""
    ascii_chars = sum(1 for c in text if ord(c) < 128)
    return int(ascii_chars / 4 + (len(text) - ascii_chars) / 1.6) + 1


def _pdf_text(path):
    if shutil.which("pdftotext"):
        done = subprocess.run(["pdftotext", "-enc", "UTF-8", str(path), "-"], capture_output=True, timeout=300)
        if done.returncode != 0:
            raise Stop(ERROR, f"pdftotext failed: {done.stderr.decode(errors='replace').strip()[:300]}")
        return done.stdout.decode("utf-8", errors="replace")
    try:
        import pypdf  # optional
    except ImportError:
        raise Stop(ERROR, "cannot read PDF text: install poppler-utils (pdftotext) or the pypdf package, "
                          "then run this again") from None
    reader = pypdf.PdfReader(str(path))
    return "\f".join(page.extract_text() or "" for page in reader.pages)


def _pdf_pages(path, text):
    if shutil.which("pdfinfo"):
        done = subprocess.run(["pdfinfo", str(path)], capture_output=True, timeout=60, text=True)
        match = re.search(r"^Pages:\s+(\d+)", done.stdout, re.M)
        if match:
            return int(match.group(1))
    return text.count("\f") + (0 if text.endswith("\f") else 1)


def _strip_markup(text):
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", "", text)
    text = re.sub(r"(?i)<br\s*/?>|</(p|div|h\d|li|tr)>", "\n", text)
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def _docx_text(path):
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", errors="replace")
    xml = re.sub(r"</w:p>", "\n\n", xml)
    xml = re.sub(r"<w:tab/>", "\t", xml)
    return html.unescape(re.sub(r"<[^>]+>", "", xml))


def _epub_text(path):
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        order = []
        opf = next((n for n in names if n.endswith(".opf")), None)
        if opf:
            data = z.read(opf).decode("utf-8", errors="replace")
            items = dict(re.findall(r'<item\b[^>]*\bid="([^"]+)"[^>]*\bhref="([^"]+)"', data))
            items |= {k: v for v, k in re.findall(r'<item\b[^>]*\bhref="([^"]+)"[^>]*\bid="([^"]+)"', data)}
            base = opf.rsplit("/", 1)[0] + "/" if "/" in opf else ""
            order = [base + items[i] for i in re.findall(r'<itemref\b[^>]*\bidref="([^"]+)"', data) if i in items]
        order = [n for n in order if n in names] or sorted(n for n in names if n.endswith((".xhtml", ".html", ".htm")))
        return "\n\n".join(_strip_markup(z.read(n).decode("utf-8", errors="replace")) for n in order)


def _mail_text(path):
    import email
    import mailbox
    from email import policy

    def one(message):
        head = "\n".join(f"{k}: {message[k]}" for k in ("From", "To", "Date", "Subject") if message[k])
        body = message.get_body(preferencelist=("plain", "html")) if hasattr(message, "get_body") else None
        content = body.get_content() if body else ""
        if body is not None and body.get_content_type() == "text/html":
            content = _strip_markup(content)
        return head + "\n\n" + content.strip()

    if path.suffix.lower() == ".mbox":
        box = mailbox.mbox(str(path), factory=lambda f: email.message_from_binary_file(f, policy=policy.default))
        return "\n\n\f".join(one(m) for m in box)
    with open(path, "rb") as handle:
        return one(email.message_from_binary_file(handle, policy=policy.default))


def extract(path):
    """(text, pages or None). Pages are separated by form feeds when the format has them."""
    path = Path(path)
    if not path.is_file():
        raise Stop(ERROR, f"no such file: {path}")
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        text = _pdf_text(path)
        return text, _pdf_pages(path, text)
    if suffix == ".docx":
        return _docx_text(path), None
    if suffix == ".epub":
        return _epub_text(path), None
    if suffix in (".eml", ".mbox"):
        return _mail_text(path), None
    raw = path.read_bytes()
    if b"\0" in raw[:65536]:
        raise Stop(ERROR, f"{path.name} looks binary; convert it to text, PDF or DOCX first")
    text = raw.decode("utf-8", errors="replace")
    if suffix in (".html", ".htm"):
        text = _strip_markup(text)
    return text, None


def measure_text(text, pages):
    chars = len(text.replace("\f", ""))
    tokens = estimate_tokens(text)
    scanned = bool(pages) and chars / pages < 100
    return {"pages": pages, "characters": chars, "words": len(text.split()), "tokens": tokens,
            "tier": "small" if tokens < SMALL_TOKENS else "large", "scanned": scanned}


# ---- chunking and estimates --------------------------------------------------------------------------------------
def _pieces(text, limit):
    """Paragraphs with their page numbers; a paragraph over the limit is split by sentences, then by characters."""
    for page_no, page in enumerate(text.split("\f"), 1):
        for para in re.split(r"\n\s*\n", page):
            para = para.strip()
            if not para:
                continue
            if estimate_tokens(para) <= limit:
                yield para, page_no
                continue
            buffer = ""
            for sentence in re.split(r"(?<=[.!?。])\s+", para):
                while estimate_tokens(sentence) > limit:  # one giant "sentence": cut by characters
                    cut = max(1, int(len(sentence) * limit / estimate_tokens(sentence)))
                    if buffer:
                        yield buffer, page_no
                        buffer = ""
                    yield sentence[:cut], page_no
                    sentence = sentence[cut:]
                if buffer and estimate_tokens(buffer + " " + sentence) > limit:
                    yield buffer, page_no
                    buffer = ""
                buffer = f"{buffer} {sentence}".strip()
            if buffer:
                yield buffer, page_no


def split_chunks(text, chunk_tokens=CHUNK_TOKENS):
    """[{"text", "tokens", "pages": [first, last]}] in document order, each at most about chunk_tokens."""
    chunks, current, first, last, size = [], [], None, None, 0
    for para, page in _pieces(text, chunk_tokens):
        tokens = estimate_tokens(para)
        if current and size + tokens > chunk_tokens:
            chunks.append({"text": "\n\n".join(current), "tokens": size, "pages": [first, last]})
            current, first, size = [], None, 0
        current.append(para)
        first = page if first is None else first
        last, size = page, size + tokens
    if current:
        chunks.append({"text": "\n\n".join(current), "tokens": size, "pages": [first, last]})
    return chunks


def chunk_units(tokens, job, subagents):
    ratio = JOBS[job][0]
    units = tokens + PROMPT_OVERHEAD + OUTPUT_WEIGHT * tokens * ratio
    return units * (CHEAP_WEIGHT if subagents else 1)


def estimate(chunk_tokens, job, subagents):
    """Rough cost of a job as a share of the 5-hour limit: chunk work (cheap model) plus the final pass (strong)."""
    ratio, finish = JOBS[job]
    if finish == "search":  # Q&A reads only the most relevant chunks, with the strong model
        best = sorted(chunk_tokens, reverse=True)[:QA_TOP]
        chunk_part, final = 0, sum(best) + PROMPT_OVERHEAD + OUTPUT_WEIGHT * 800
    else:
        chunk_part = sum(chunk_units(t, job, subagents) for t in chunk_tokens)
        outputs = sum(t * ratio for t in chunk_tokens)
        if finish == "combine":
            levels = 1
            size = outputs
            while size > COMBINE_BUDGET:  # hierarchical: each level shrinks to about a quarter
                size, levels = size / 4, levels + 1
            final = (outputs * 1.3 + PROMPT_OVERHEAD * levels) + OUTPUT_WEIGHT * min(outputs, 4000)
        else:  # assemble: joined by the tool; a strong model only rereads a sample for style
            final = PROMPT_OVERHEAD + min(outputs, 4000) * (1 + OUTPUT_WEIGHT)
    total = chunk_part + final
    return {"chunks": len(chunk_tokens), "tokens": sum(chunk_tokens), "units": int(total),
            "percent": round(100 * total / LIMIT_UNITS, 1),
            "models": {"chunks": "cheap (Haiku) sub-agents" if subagents else "this session, one chunk at a time",
                       "final": "strong model (final combine and human-style rewrite)"},
            "needs_confirmation": 100 * total / LIMIT_UNITS > CONFIRM_PERCENT}


# ---- work folder -------------------------------------------------------------------------------------------------
def root_dir():
    configured = os.getenv("OFFICE_ROOT")
    if configured:
        return Path(configured).resolve()
    try:
        done = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=20)
        if done.returncode == 0 and done.stdout.strip():
            return Path(done.stdout.strip()).resolve()
    except (OSError, subprocess.SubprocessError):
        pass
    return Path.cwd().resolve()


def work_root():
    base = root_dir() / ".linko-work"
    base.mkdir(parents=True, exist_ok=True)
    ignore = base / ".gitignore"
    if not ignore.exists():
        ignore.write_text("# Office harness working memory: never committed or synced.\n*\n")
    return base


def work_dir(source):
    source = Path(source).resolve()
    slug = re.sub(r"[^\w.-]+", "-", source.stem, flags=re.UNICODE).strip("-")[:40] or "doc"
    return work_root() / f"{slug}-{hashlib.sha256(str(source).encode()).hexdigest()[:8]}"


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load_state(work):
    path = work / "state.json"
    if not path.exists():
        raise Stop(ERROR, "no plan yet: run `office.py plan SOURCE` first")
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(work, state):
    tmp = work / "state.json.tmp"
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(work / "state.json")


def spent_percent(state):
    units = sum(c["units"] * c["attempts"] for c in state["chunks"])
    return 100 * units / LIMIT_UNITS


def progress(state):
    done = sum(c["status"] == "done" for c in state["chunks"])
    return f"{done}/{len(state['chunks'])}"


def plan(source, job="summary", chunk_tokens=CHUNK_TOKENS, subagents=True, fresh=False):
    """Create (or resume) the work folder. Returns (state, lines to print)."""
    if job not in JOBS:
        raise Stop(ERROR, f"unknown job {job}; choose one of {', '.join(JOBS)}")
    source = Path(source).resolve()
    work = work_dir(source)
    digest = sha256_file(source)
    if (work / "state.json").exists() and not fresh:
        state = load_state(work)
        if state["source_sha256"] != digest:
            raise Stop(ERROR, "the source changed since the saved plan; run plan again with --fresh to start over")
        if state["job"] != job:
            raise Stop(ERROR, f"the saved plan is for job {state['job']}; use --fresh to plan job {job}")
        return state, [f"resuming: {progress(state)} chunks done, saved work in {work}"]
    if work.exists():
        shutil.rmtree(work)
    text, pages = extract(source)
    info = measure_text(text, pages)
    if info["scanned"]:
        raise Stop(STOP, f"STOP: {source.name} has {pages} pages but almost no text layer (scanned). OCR it as a "
                         "separate step only if the owner agrees (for example `ocrmypdf in.pdf out.pdf`).")
    (work / "chunks").mkdir(parents=True)
    (work / "parts").mkdir()
    (work / "source.txt").write_text(text, encoding="utf-8")
    pieces = split_chunks(text, chunk_tokens) if info["tier"] == "large" else [
        {"text": text.replace("\f", "\n\n"), "tokens": info["tokens"], "pages": [1, pages or 1]}]
    chunks = []
    for n, piece in enumerate(pieces, 1):
        name = f"{n:04d}"
        (work / "chunks" / f"{name}.txt").write_text(piece["text"], encoding="utf-8")
        chunks.append({"id": n, "tokens": piece["tokens"], "pages": piece["pages"], "status": "pending",
                       "attempts": 0, "units": int(chunk_units(piece["tokens"], job, subagents)), "errors": []})
    cost = estimate([c["tokens"] for c in chunks], job, subagents)
    state = {"source": str(source), "source_sha256": digest, "job": job, "subagents": subagents,
             "measure": info, "estimate": cost, "confirmed": not cost["needs_confirmation"],
             "cap_percent": CAP_PERCENT, "status": "planned", "chunks": chunks}
    save_state(work, state)
    lines = [f"document: {source.name}, {pages or '?'} pages, {info['characters']} characters, "
             f"about {info['tokens']} tokens ({info['tier']})",
             f"estimate: {cost['chunks']} chunk(s), about {cost['percent']}% of the 5-hour limit; "
             f"chunks: {cost['models']['chunks']}; final: {cost['models']['final']}",
             f"work folder: {work}"]
    if info["tier"] == "small":
        lines.append("small document: handle it directly from chunks/0001.txt, no chunking needed")
    if cost["needs_confirmation"]:
        lines.append(f"STOP: the estimate is above {CONFIRM_PERCENT:g}% of the 5-hour limit. Report it to the owner "
                     "and wait; after their confirmation run `office.py confirm SOURCE`.")
    return state, lines


def handout(source, batch=1):
    work = work_dir(source)
    state = load_state(work)
    if state["status"] == "stopped":
        raise Stop(LOOP, f"STOP: {state.get('reason', 'stopped by the loop guard')}")
    if not state["confirmed"]:
        raise Stop(STOP, f"STOP: waiting for the owner to confirm the estimate ({state['estimate']['percent']}% of "
                         "the 5-hour limit); then run `office.py confirm SOURCE`")
    lines, given = [], 0
    total = len(state["chunks"])
    for chunk in state["chunks"]:
        if given >= batch:
            break
        if chunk["status"] == "done":
            continue
        if chunk["attempts"] >= MAX_ATTEMPTS:
            state["status"], state["reason"] = "stopped", (
                f"loop guard: chunk {chunk['id']}/{total} failed {chunk['attempts']} times "
                f"({'; '.join(chunk['errors'][-2:]) or 'no output written'}); progress saved at {progress(state)}")
            save_state(work, state)
            raise Stop(LOOP, "STOP: " + state["reason"])
        if spent_percent(state) + 100 * chunk["units"] / LIMIT_UNITS > state["cap_percent"]:
            state["status"] = "capped"
            save_state(work, state)
            if given:
                break
            raise Stop(STOP, f"STOP: budget cap of {state['cap_percent']:g}% reached at {progress(state)} chunks; "
                             "progress is saved. Report it; to go on, the owner confirms a higher cap "
                             "(`office.py confirm SOURCE --cap PCT`) and the task resumes from here.")
        if chunk["status"] == "in_progress":
            chunk["errors"].append("handed out again without done/fail")
        chunk["status"], chunk["attempts"] = "in_progress", chunk["attempts"] + 1
        name = f"{chunk['id']:04d}"
        lines.append(f"chunk {chunk['id']}/{total} (pages {chunk['pages'][0]}-{chunk['pages'][1]}, attempt "
                     f"{chunk['attempts']}/{MAX_ATTEMPTS}): input {work / 'chunks' / (name + '.txt')} "
                     f"output {work / 'parts' / (name + '.md')}")
        given += 1
    if not given:
        lines.append(f"all {total} chunks done; next: `office.py {JOBS[state['job']][1]} SOURCE ...`")
    state["status"] = "running" if given else "chunks_done"
    save_state(work, state)
    return lines


def mark_done(source, chunk_id):
    work = work_dir(source)
    state = load_state(work)
    chunk = _chunk(state, chunk_id)
    part = work / "parts" / f"{chunk_id:04d}.md"
    if not part.is_file() or not part.read_text(encoding="utf-8").strip():
        raise Stop(ERROR, f"write the chunk's result to {part} first")
    digest = hashlib.sha256(part.read_bytes()).hexdigest()
    for other in state["chunks"]:
        if other["id"] != chunk_id and other.get("sha256") == digest:
            chunk["errors"].append(f"output identical to chunk {other['id']}")
            part.unlink()
            save_state(work, state)
            raise Stop(LOOP if chunk["attempts"] >= MAX_ATTEMPTS else ERROR,
                       f"chunk {chunk_id} output is identical to chunk {other['id']} (a repeated step); "
                       "process the chunk's own text again")
    chunk["status"], chunk["sha256"] = "done", digest
    save_state(work, state)
    return [f"chunk {chunk_id}/{len(state['chunks'])} done; progress {progress(state)}, "
            f"spent about {spent_percent(state):.1f}% of the 5-hour limit"]


def mark_failed(source, chunk_id, reason):
    work = work_dir(source)
    state = load_state(work)
    chunk = _chunk(state, chunk_id)
    chunk["status"] = "failed"
    chunk["errors"].append(reason or "failed")
    save_state(work, state)
    left = MAX_ATTEMPTS - chunk["attempts"]
    return [f"chunk {chunk_id} attempt {chunk['attempts']} failed; {max(left, 0)} retr{'y' if left == 1 else 'ies'} left"]


def _chunk(state, chunk_id):
    for chunk in state["chunks"]:
        if chunk["id"] == chunk_id:
            return chunk
    raise Stop(ERROR, f"no chunk {chunk_id}; the plan has 1-{len(state['chunks'])}")


def confirm(source, cap=None):
    work = work_dir(source)
    state = load_state(work)
    state["confirmed"] = True
    state["cap_percent"] = float(cap) if cap is not None else max(state["cap_percent"],
                                                                 round(state["estimate"]["percent"] * 1.5, 1))
    if state["status"] in ("capped", "stopped"):
        state["status"] = "running"
        state.pop("reason", None)
        for chunk in state["chunks"]:  # an owner-confirmed resume gives stuck chunks a fresh retry budget
            if chunk["status"] != "done" and chunk["attempts"] >= MAX_ATTEMPTS:
                chunk["attempts"] = MAX_ATTEMPTS - 1
    save_state(work, state)
    return [f"confirmed; budget cap {state['cap_percent']:g}% of the 5-hour limit"]


def status(source):
    work = work_dir(source)
    state = load_state(work)
    lines = [f"job {state['job']}: {progress(state)} chunks done, status {state['status']}, spent about "
             f"{spent_percent(state):.1f}% of {state['cap_percent']:g}% cap (estimate {state['estimate']['percent']}%)"]
    if state.get("reason"):
        lines.append("reason: " + state["reason"])
    return lines


# ---- finishing ---------------------------------------------------------------------------------------------------
WORD = re.compile(r"\w{3,}", re.UNICODE)


def search(source, query, top=QA_TOP):
    """Chunks ranked by keyword hits; a prefix match lets inflected Georgian or English forms count."""
    work = work_dir(source)
    state = load_state(work)
    terms = {w.lower() for w in WORD.findall(query)}
    stems = {t[:max(4, len(t) - 2)] for t in terms}
    scored = []
    for chunk in state["chunks"]:
        words = [w.lower() for w in WORD.findall((work / "chunks" / f"{chunk['id']:04d}.txt").read_text("utf-8"))]
        score = sum(3 if w in terms else 1 for w in words if w in terms or any(w.startswith(s) for s in stems))
        if score:
            scored.append((score, chunk["id"]))
    scored.sort(key=lambda item: (-item[0], item[1]))
    if not scored:
        return ["no chunk matches; try other keywords or synonyms"]
    return [f"chunk {cid} score {score}: {work / 'chunks' / f'{cid:04d}.txt'}" for score, cid in scored[:top]]


def combine(source, budget=COMBINE_BUDGET):
    """Final-pass input. If the chunk results are too big for one pass, write groups to summarize first (one level
    at a time); run again after every group's ``.summary.md`` exists."""
    work = work_dir(source)
    state = load_state(work)
    if any(c["status"] != "done" for c in state["chunks"]):
        raise Stop(ERROR, f"only {progress(state)} chunks are done; finish them first (`office.py next SOURCE`)")
    inputs = []
    for c in state["chunks"]:
        label = f"[chunk {c['id']}, pages {c['pages'][0]}-{c['pages'][1]}]"
        inputs.append((label, (work / "parts" / f"{c['id']:04d}.md").read_text("utf-8").strip()))
    level = 0
    base = work / "combine"
    while True:
        if sum(estimate_tokens(text) for _, text in inputs) <= budget or len(inputs) == 1:
            final = base / "final-input.md"
            final.parent.mkdir(exist_ok=True)
            final.write_text("\n\n".join(f"{label}\n{text}" for label, text in inputs) + "\n", encoding="utf-8")
            return [f"final pass: read {final} (about {estimate_tokens(final.read_text('utf-8'))} tokens) "
                    "with the strong model and write the result to a new file"]
        level += 1
        folder = base / f"level{level}"
        groups, current, size = [], [], 0
        for label, text in inputs:
            tokens = estimate_tokens(text)
            if current and size + tokens > budget:
                groups.append(current)
                current, size = [], 0
            current.append((label, text))
            size += tokens
        groups.append(current)
        folder.mkdir(parents=True, exist_ok=True)
        missing, summaries = [], []
        for n, group in enumerate(groups, 1):
            path = folder / f"group-{n:03d}.md"
            body = "\n\n".join(f"{label}\n{text}" for label, text in group) + "\n"
            if not path.exists() or path.read_text("utf-8") != body:
                path.write_text(body, encoding="utf-8")
            done = folder / f"group-{n:03d}.summary.md"
            if done.is_file() and done.read_text("utf-8").strip():
                summaries.append((f"[group {level}.{n}: {group[0][0]} to {group[-1][0]}]",
                                  done.read_text("utf-8").strip()))
            else:
                missing.append(f"group {n}/{len(groups)}: input {path} output {done}")
        if missing:
            return [f"hierarchical combine level {level}: summarize each group (cheap model) into its output, then "
                    "run combine again", *missing]
        inputs = summaries


def assemble(source, out):
    work = work_dir(source)
    state = load_state(work)
    if any(c["status"] != "done" for c in state["chunks"]):
        raise Stop(ERROR, f"only {progress(state)} chunks are done; finish them first")
    out = Path(out)
    guard_new_output(source, out)
    text = "\n\n".join((work / "parts" / f"{c['id']:04d}.md").read_text("utf-8").strip() for c in state["chunks"])
    out.write_text(text + "\n", encoding="utf-8")
    register_output(out)
    return [f"wrote {out}"]


def guard_new_output(source, out):
    out = Path(out).resolve()
    if out == Path(source).resolve():
        raise Stop(ERROR, "refusing to write over the original document")
    if out.exists() and out not in registered_outputs():
        raise Stop(ERROR, f"{out.name} already exists and is not an output of this harness; pick a new name "
                          "(`office.py outpath SOURCE KIND`)")


def output_path(source, kind, ext=".md", directory=None, name=None):
    source = Path(source).resolve()
    kind = re.sub(r"[\\/\0]+", " ", kind).strip() or "output"
    ext = ext if ext.startswith(".") else "." + ext
    folder = Path(directory).resolve() if directory else source.parent
    stem = Path(re.sub(r"[\\/\0]+", " ", name)).stem.strip() if name else source.stem
    stem = stem or source.stem
    candidate = folder / f"{stem} {kind}{ext}"
    n = 2
    while candidate.exists():
        candidate = folder / f"{stem} {kind} {n}{ext}"
        n += 1
    register_output(candidate)
    return candidate


def registered_outputs():
    path = work_root() / "outputs.json"
    return {Path(p) for p in json.loads(path.read_text("utf-8"))} if path.exists() else set()


def register_output(path):
    outputs = registered_outputs() | {Path(path).resolve()}
    (work_root() / "outputs.json").write_text(json.dumps(sorted(map(str, outputs)), ensure_ascii=False, indent=1),
                                              encoding="utf-8")


# ---- dash fix ----------------------------------------------------------------------------------------------------
DASHES = "—–"
FENCE = re.compile(r"^\s*(```|~~~)")
TABLE = re.compile(r"^\s*\|")
PROTECT = re.compile(r"`[^`\n]*`|<?(?:https?|ftp)://[^\s<>)\]]+>?|\bwww\.[^\s<>)\]]+|\[[^\]\n]*\]\([^)\n]*\)")


def fix_line(line):
    """Replace em and en dashes in one prose line; inline code, URLs and links are left as they are."""
    if not any(d in line for d in DASHES):
        return line
    saved = []

    def keep(match):
        saved.append(match.group(0))
        return f"\0{len(saved) - 1}\0"

    text = PROTECT.sub(keep, line)
    text = re.sub(rf"(\d)\s*[{DASHES}]\s*(?=\d)", r"\1-", text)                 # 2020–2024 -> 2020-2024
    text = re.sub(rf"^(\s*(?:>\s*)*)[{DASHES}]\s*", r"\1- ", text)               # dialogue dash at line start
    text = re.sub(rf"\s*[{DASHES}]+\s*", ", ", text)                             # any other dash -> comma
    text = re.sub(r",\s*([,.;:!?…)\]»“”\"])", r"\1", text)                       # ", ." -> "."
    text = re.sub(r"([,;:(\[«„])\s*,\s*", r"\1 ", text)                          # "word,, x" / "(, x"
    text = re.sub(r"(?<=\S) {2,}", " ", text)
    text = re.sub(r",\s*$", ",", text)
    text = re.sub(r"\(\s+", "(", text)
    return re.sub(r"\0(\d+)\0", lambda m: saved[int(m.group(1))], text)


def fix_dashes(text):
    """Em/en dashes to keyboard punctuation, skipping fenced code blocks and markdown tables."""
    out, fence = [], None
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        match = FENCE.match(body)
        if fence:
            if match and match.group(1) == fence:
                fence = None
            out.append(line)
            continue
        if match:
            fence = match.group(1)
            out.append(line)
            continue
        if TABLE.match(body):
            out.append(line)
            continue
        out.append(fix_line(body) + line[len(body):])
    return "".join(out)


def dashfix(path, out=None):
    path = Path(path).resolve()
    target = Path(out).resolve() if out else path
    if target == path and not out and not _may_rewrite(path):
        raise Stop(ERROR, f"{path.name} is not a new output of this harness (it is tracked in git and was not made "
                          "with `office.py outpath`); pass --out NEW_FILE so the original stays untouched")
    if out and target.exists() and target not in registered_outputs():
        raise Stop(ERROR, f"{target.name} already exists; pick a new output name")
    text = path.read_text(encoding="utf-8")
    fixed = fix_dashes(text)
    target.write_text(fixed, encoding="utf-8")
    register_output(target)
    count = sum(text.count(d) for d in DASHES) - sum(fixed.count(d) for d in DASHES)
    return [f"dashfix: replaced {count} dash(es) in {target}"]


def _may_rewrite(path):
    if path in registered_outputs():
        return True
    try:
        done = subprocess.run(["git", "ls-files", "--error-unmatch", path.name], cwd=path.parent,
                              capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode != 0 and b"not a git repository" not in done.stderr.lower()


# ---- CLI ---------------------------------------------------------------------------------------------------------
def main(argv=None):
    parser = argparse.ArgumentParser(prog="office.py", description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("measure").add_argument("source")
    p = sub.add_parser("plan")
    p.add_argument("source")
    p.add_argument("--job", default="summary", choices=sorted(JOBS))
    p.add_argument("--chunk-tokens", type=int, default=CHUNK_TOKENS)
    p.add_argument("--no-subagents", action="store_true")
    p.add_argument("--fresh", action="store_true")
    p = sub.add_parser("confirm")
    p.add_argument("source")
    p.add_argument("--cap", type=float)
    p = sub.add_parser("next")
    p.add_argument("source")
    p.add_argument("--batch", type=int, default=1)
    p = sub.add_parser("done")
    p.add_argument("source")
    p.add_argument("id", type=int)
    p = sub.add_parser("fail")
    p.add_argument("source")
    p.add_argument("id", type=int)
    p.add_argument("--reason", default="")
    sub.add_parser("status").add_argument("source")
    p = sub.add_parser("search")
    p.add_argument("source")
    p.add_argument("query")
    p.add_argument("--top", type=int, default=QA_TOP)
    p = sub.add_parser("combine")
    p.add_argument("source")
    p.add_argument("--budget", type=int, default=COMBINE_BUDGET)
    p = sub.add_parser("assemble")
    p.add_argument("source")
    p.add_argument("out")
    p = sub.add_parser("outpath")
    p.add_argument("source")
    p.add_argument("kind")
    p.add_argument("--ext", default=".md")
    p.add_argument("--dir")
    p.add_argument("--name")
    p = sub.add_parser("dashfix")
    p.add_argument("file")
    p.add_argument("--out")
    args = parser.parse_args(argv)
    try:
        if args.command == "measure":
            text, pages = extract(args.source)
            info = measure_text(text, pages)
            lines = [json.dumps(info)]
            if info["scanned"]:
                lines.append("STOP: scanned PDF without a text layer; offer OCR to the owner as a separate step")
        elif args.command == "plan":
            _, lines = plan(args.source, args.job, args.chunk_tokens, not args.no_subagents, args.fresh)
            if any(line.startswith("STOP:") for line in lines):
                print("\n".join(lines))
                return STOP
        elif args.command == "confirm":
            lines = confirm(args.source, args.cap)
        elif args.command == "next":
            lines = handout(args.source, max(1, args.batch))
        elif args.command == "done":
            lines = mark_done(args.source, args.id)
        elif args.command == "fail":
            lines = mark_failed(args.source, args.id, args.reason)
        elif args.command == "status":
            lines = status(args.source)
        elif args.command == "search":
            lines = search(args.source, args.query, args.top)
        elif args.command == "combine":
            lines = combine(args.source, args.budget)
        elif args.command == "assemble":
            lines = assemble(args.source, args.out)
        elif args.command == "outpath":
            lines = [str(output_path(args.source, args.kind, args.ext, args.dir, args.name))]
        else:
            lines = dashfix(args.file, args.out)
    except Stop as stop:
        print(str(stop), file=sys.stdout if stop.code in (STOP, LOOP) else sys.stderr)
        return stop.code
    print("\n".join(lines))
    return OK


if __name__ == "__main__":
    sys.exit(main())
