#!/usr/bin/env python3
"""Humanizer level 2: add a few human-looking typos and dropped punctuation to a text, with no AI.

Standard library only (it runs with whatever python3 the worker has) and deterministic for a given --seed. It reads
the input and writes a NEW file next to it ("<name> humanized.<ext>"); the original is never changed.

  humanize.py humanize FILE [--typos PCT] [--punct PCT] [--seed N] [--out NEW]
  humanize.py measure FILE        style statistics of a writing sample (as the Writing settings store them)
  (also: python -m ai_manager.humanize humanize FILE ...)

--typos is the share of eligible words that get one typo (default 0.2%); --punct is the share of eligible punctuation
marks (, ; : . ! ?) that are deleted (default 2%). A typo is exactly one of: insert a letter (a keyboard neighbour on
the English QWERTY or the Georgian standard layout), delete a letter, or swap two adjacent letters; a word gets at
most one. Punctuation is only ever deleted, never inserted, and the last mark of a line or paragraph is kept.

Never touched: any token containing a digit, words of 1-2 letters, capitalised words (names; sentence-initial words
are skipped too, since a name can open a sentence), URLs, emails, paths and other code-like tokens, inline code and
fenced code blocks, text inside quotes, and markdown table rows. Supports .txt, .md and .docx (the text of
word/document.xml, edited in place inside a copy of the package; tables are left alone).

Exit codes: 0 ok, 2 error.
"""

import argparse
import html
import json
import random
import re
import sys
import zipfile
from pathlib import Path

DEFAULT_TYPOS = 0.2   # percent of eligible words
DEFAULT_PUNCT = 2.0   # percent of eligible punctuation marks
MAX_TYPOS, MAX_PUNCT = 10.0, 50.0
SUFFIXES = (".txt", ".md", ".markdown", ".docx")
PUNCT = ",;:.!?"
FINAL = ".!?…"
CLOSERS = "\"'”’»)]*_"
OK, ERROR = 0, 2

# ---- keyboards ---------------------------------------------------------------------------------------------------
ROWS = ("qwertyuiop", "asdfghjkl", "zxcvbnm")
# The Georgian standard layout puts each letter on the Latin key of the same sound; shifted letters share the key.
GEORGIAN = {"q": "ქ", "w": "წ", "e": "ე", "r": "რ", "t": "ტ", "y": "ყ", "u": "უ", "i": "ი", "o": "ო", "p": "პ",
            "a": "ა", "s": "ს", "d": "დ", "f": "ფ", "g": "გ", "h": "ჰ", "j": "ჯ", "k": "კ", "l": "ლ",
            "z": "ზ", "x": "ხ", "c": "ც", "v": "ვ", "b": "ბ", "n": "ნ", "m": "მ"}
GEORGIAN_SHIFT = {"W": "ჭ", "R": "ღ", "T": "თ", "S": "შ", "J": "ჟ", "Z": "ძ", "C": "ჩ"}


def _latin_neighbours():
    where = {key: (r, c) for r, row in enumerate(ROWS) for c, key in enumerate(row)}
    near = {}
    for key, (r, c) in where.items():
        found = []
        for other, (r2, c2) in where.items():
            # Rows are staggered by about half a key: the row below sits half a key to the right.
            if other != key and abs(r - r2) <= 1 and abs((c + 0.5 * r) - (c2 + 0.5 * r2)) <= 1.0:
                found.append(other)
        near[key] = "".join(sorted(found))
    return near


LATIN_NEAR = _latin_neighbours()
GEORGIAN_KEY = {v: k for k, v in GEORGIAN.items()} | {v: k.lower() for k, v in GEORGIAN_SHIFT.items()}
GEORGIAN_NEAR = {letter: "".join(GEORGIAN[n] for n in LATIN_NEAR[key]) for letter, key in GEORGIAN_KEY.items()}
GEORGIAN_LETTERS = "".join(sorted(set(GEORGIAN.values()) | set(GEORGIAN_SHIFT.values())))


def neighbours(letter):
    """Keys next to this letter on its keyboard (English QWERTY or Georgian standard), in that script."""
    if letter in GEORGIAN_NEAR:
        return GEORGIAN_NEAR[letter]
    return LATIN_NEAR.get(letter, "")


def is_georgian(letter):
    return "Ⴀ" <= letter <= "ჿ" or "Ა" <= letter <= "Ჿ" or "ⴀ" <= letter <= "⴯"


# ---- protection --------------------------------------------------------------------------------------------------
FENCE = re.compile(r"^\s*(```|~~~)")
TABLE_ROW = re.compile(r"^\s*\|.*$|^\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$|^[^\n|]*\|[^\n]*\|[^\n]*$")
SPANS = re.compile("|".join((
    r"`[^`\n]*`",                                        # inline code
    r"<?(?:https?|ftp|file|mailto):[^\s<>\"]+>?",          # URLs
    r"\bwww\.[^\s<>\"]+",
    r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+",                      # emails
    r"\]\([^)\n]*\)",                                     # markdown link targets
    r"<[^<>\n]+>",                                        # html or xml tags, autolinks
    r"\"(?:[^\"\n]|\n(?![ \t]*\n))*\"",                   # quotes, inside one paragraph
    r"“(?:[^”\n]|\n(?![ \t]*\n))*”",
    r"„(?:[^“”\n]|\n(?![ \t]*\n))*[“”]",
    r"«(?:[^»\n]|\n(?![ \t]*\n))*»",
    r"‘(?:[^’\n]|\n(?![ \t]*\n))*’",
    r"(?<!\w)'(?:[^'\n]|\n(?![ \t]*\n))*'(?!\w)",
)))
CHUNK = re.compile(r"\S+")
CODE_CHARS = set("@/\\_=<>{}[]|#$%^&~`+")
WORD = re.compile(r"[^\W\d_]+")


def protected_mask(text, markdown=True):
    """True for every character the humanizer must leave exactly as it is."""
    mask = bytearray(len(text))

    def protect(start, end):
        mask[start:end] = b"\x01" * (end - start)

    pos, fenced, fence = 0, False, ""
    for line in text.splitlines(keepends=True):
        end, body = pos + len(line), line.rstrip("\r\n")
        marker = FENCE.match(body)
        if fenced:
            protect(pos, end)
            if marker and marker.group(1) == fence:
                fenced = False
        elif marker:
            fenced, fence = True, marker.group(1)
            protect(pos, end)
        elif TABLE_ROW.match(body) or (markdown and re.match(r"^( {4}|\t)\S", body)):
            protect(pos, end)
        pos = end
    for match in SPANS.finditer(text):
        protect(*match.span())
    for match in CHUNK.finditer(text):
        chunk = match.group()
        if any(c.isdigit() for c in chunk) or CODE_CHARS & set(chunk.strip("*_()[]\"'“”„«»‘’.,;:!?")) or \
                re.search(r"\w[.:]\w", chunk):
            protect(*match.span())  # numbers, versions, paths, file names, identifiers: the whole token
    return mask


# ---- edits -------------------------------------------------------------------------------------------------------
def pick_count(eligible, rate, rng):
    """How many of `eligible` items to change at `rate` percent; the fractional part is a weighted coin."""
    exact = eligible * rate / 100.0
    count = int(exact)
    if rng.random() < exact - count:
        count += 1
    return min(count, eligible)


def eligible_words(text, mask):
    spans = []
    for match in WORD.finditer(text):
        start, end = match.span()
        word = match.group()
        if len(word) < 3 or any(mask[start:end]):
            continue
        if any(c.isupper() for c in word) or not word.isalpha():
            continue  # names and acronyms; sentence-initial words too, since a name can open a sentence
        if (start and text[start - 1] in "'’-") or (end < len(text) and text[end] in "'’-"):
            continue  # parts of contractions and hyphenated compounds stay whole
        spans.append((start, end))
    return spans


def eligible_marks(text, mask):
    """Punctuation that may be dropped: followed by a space or line end, not inside a protected span or a run of
    marks (ellipses, ?!), and never the last mark of a line or paragraph."""
    found = []
    for i, c in enumerate(text):
        if c not in PUNCT or mask[i]:
            continue
        before = text[i - 1] if i else ""
        after = text[i + 1] if i + 1 < len(text) else ""
        if not before or not (before.isalpha() or before in "\"'”’»)"):
            continue
        if before in PUNCT or after in PUNCT or after not in (" ", "\t"):
            continue  # a line end (paragraph or line final), a run of marks, or a mark inside a token
        rest = text[i + 1:].split("\n", 1)[0]
        if c in FINAL and not rest.strip(" \t" + CLOSERS):
            continue
        found.append(i)
    return found


def typo(word, rng):
    """Exactly one error: insert a neighbouring key, delete a letter or swap two adjacent different letters."""
    kinds = ["insert", "delete"]
    swaps = [i for i in range(len(word) - 1) if word[i] != word[i + 1]]
    if swaps:
        kinds.append("swap")
    kind = rng.choice(kinds)
    if kind == "delete":
        i = rng.randrange(len(word))
        return kind, word[:i] + word[i + 1:]
    if kind == "swap":
        i = rng.choice(swaps)
        return kind, word[:i] + word[i + 1] + word[i] + word[i + 2:]
    i = rng.randrange(1, len(word) + 1)  # after letter i-1: a finger slipping onto the next key
    base = word[i - 1]
    near = neighbours(base)
    if near:
        letter = rng.choice(near)
    elif is_georgian(base):
        letter = rng.choice(GEORGIAN_LETTERS)
    else:
        letter = rng.choice("abcdefghijklmnopqrstuvwxyz")
    return kind, word[:i] + letter + word[i:]


def plan(text, typos=DEFAULT_TYPOS, punct=DEFAULT_PUNCT, seed=None, markdown=True, mask=None):
    """The edits for `text`: {"words": [(start, end, new)], "marks": [index], counts}. Deterministic for a seed."""
    check_rates(typos, punct)
    rng = random.Random(seed)
    mask = protected_mask(text, markdown) if mask is None else mask
    words, marks = eligible_words(text, mask), eligible_marks(text, mask)
    chosen = sorted(rng.sample(range(len(words)), pick_count(len(words), typos, rng)))
    changes = []
    for n in chosen:
        start, end = words[n]
        kind, new = typo(text[start:end], rng)
        changes.append((start, end, new, kind))
    dropped = sorted(rng.sample(marks, pick_count(len(marks), punct, rng)))
    return {"words": changes, "marks": dropped, "eligible_words": len(words), "eligible_marks": len(marks)}


def apply(chars, edits):
    """Apply a plan to a list of (char, tag) pairs; inserted letters take the tag of the letter before them."""
    replace = {start: (end, new) for start, end, new, _ in edits["words"]}
    drop = set(edits["marks"])
    out, i = [], 0
    while i < len(chars):
        if i in replace:
            end, new = replace[i]
            tags = [tag for _, tag in chars[i:end]]
            tags += [tags[-1]] * (len(new) - len(tags))
            out += list(zip(new, tags))
            i = end
            continue
        if i not in drop:
            out.append(chars[i])
        i += 1
    return out


def humanize_text(text, typos=DEFAULT_TYPOS, punct=DEFAULT_PUNCT, seed=None, markdown=True):
    edits = plan(text, typos, punct, seed, markdown)
    result = "".join(c for c, _ in apply([(c, 0) for c in text], edits))
    return result, edits


def check_rates(typos, punct):
    if not 0 <= typos <= MAX_TYPOS:
        raise ValueError(f"the typo rate must be 0-{MAX_TYPOS:g}%")
    if not 0 <= punct <= MAX_PUNCT:
        raise ValueError(f"the punctuation rate must be 0-{MAX_PUNCT:g}%")


# ---- docx --------------------------------------------------------------------------------------------------------
PARAGRAPH = re.compile(r"<w:p[ >].*?</w:p>|<w:p/>", re.S)
RUN_TEXT = re.compile(r"(<w:t(?:\s[^>]*)?>)(.*?)(</w:t>)", re.S)


def _tables(xml):
    """Character ranges of top-level tables (nested ones are inside them)."""
    spans, depth, start = [], 0, 0
    for match in re.finditer(r"<w:tbl[ >]|</w:tbl>", xml):
        if match.group().startswith("</"):
            depth -= 1
            if depth == 0:
                spans.append((start, match.end()))
        else:
            if depth == 0:
                start = match.start()
            depth += 1
    return spans


def humanize_docx_xml(xml, typos, punct, seed):
    """Edit the visible text of document.xml; every tag stays as it was. Paragraphs join with blank lines so
    quotes and paragraph ends behave as in plain text; table paragraphs are protected."""
    tables = _tables(xml)
    chars, pieces = [], []  # pieces: (start, end, tag-open, tag-close) of each w:t in xml
    protected = []
    for p in PARAGRAPH.finditer(xml):
        in_table = any(a <= p.start() < b for a, b in tables)
        if chars:
            chars += [("\n", None), ("\n", None)]
            protected += [False, False]
        for run in RUN_TEXT.finditer(p.group()):
            text = html.unescape(run.group(2))
            index = len(pieces)
            pieces.append((p.start() + run.start(), p.start() + run.end(), run.group(1), run.group(3)))
            chars += [(c, index) for c in text]
            protected += [in_table] * len(text)
    text = "".join(c for c, _ in chars)
    mask = protected_mask(text, markdown=False)
    for i, flag in enumerate(protected):
        if flag:
            mask[i] = 1
    edits = plan(text, typos, punct, seed, markdown=False, mask=mask)
    new = [[] for _ in pieces]
    for c, tag in apply(chars, edits):
        if tag is not None:
            new[tag].append(c)
    out, last = [], 0
    for (start, end, opening, closing), letters in zip(pieces, new):
        value = "".join(letters)
        if value != value.strip() and "xml:space" not in opening:
            opening = opening[:-1] + ' xml:space="preserve">'
        out += [xml[last:start], opening, html.escape(value, quote=False), closing]
        last = end
    out.append(xml[last:])
    return "".join(out), edits


def humanize_docx(source, target, typos, punct, seed):
    with zipfile.ZipFile(source) as z:
        try:
            xml = z.read("word/document.xml").decode("utf-8")
        except KeyError:
            raise ValueError("not a Word document: word/document.xml is missing") from None
        new_xml, edits = humanize_docx_xml(xml, typos, punct, seed)
        with zipfile.ZipFile(target, "x") as out:
            for info in z.infolist():
                data = new_xml.encode("utf-8") if info.filename == "word/document.xml" else z.read(info)
                out.writestr(info, data)
    return edits


# ---- files -------------------------------------------------------------------------------------------------------
def output_path(source, out=None):
    source = Path(source).resolve()
    if out:
        target = Path(out).resolve()
        if target == source:
            raise ValueError("refusing to write over the original document")
        if target.exists():
            raise ValueError(f"{target.name} already exists; pick a new output name")
        if target.suffix.lower() != source.suffix.lower():
            raise ValueError(f"the output must keep the {source.suffix} extension")
        return target
    candidate, n = source.with_name(f"{source.stem} humanized{source.suffix}"), 2
    while candidate.exists():
        candidate = source.with_name(f"{source.stem} humanized {n}{source.suffix}")
        n += 1
    return candidate


def humanize_file(source, typos=DEFAULT_TYPOS, punct=DEFAULT_PUNCT, seed=None, out=None):
    """Write the humanized copy to a NEW file and return (path, edits)."""
    source = Path(source)
    if not source.is_file():
        raise ValueError(f"{source} is not a file")
    suffix = source.suffix.lower()
    if suffix not in SUFFIXES:
        raise ValueError(f"unsupported file type {suffix or '(none)'}: use .txt, .md or .docx "
                         "(convert other formats to one of these first)")
    check_rates(typos, punct)
    target = output_path(source, out)
    if suffix == ".docx":
        try:
            zipfile.ZipFile(source).close()
        except zipfile.BadZipFile:
            raise ValueError("not a Word document (.docx is a zip package)") from None
        try:
            edits = humanize_docx(source, target, typos, punct, seed)  # mode "x": never replaces a file
        except FileExistsError:
            raise ValueError(f"{target.name} already exists; pick a new output name") from None
        except BaseException:
            target.unlink(missing_ok=True)  # only our own half-written copy: the name was free a moment ago
            raise
        return target, edits
    raw = source.read_bytes()
    try:
        text = raw.decode("utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else "utf-8")
    except UnicodeDecodeError:
        raise ValueError("the file is not UTF-8 text") from None
    result, edits = humanize_text(text, typos, punct, seed, markdown=suffix != ".txt")
    with open(target, "x", encoding="utf-8", newline="") as handle:  # "x": never replaces an existing file
        handle.write(("﻿" if raw.startswith(b"\xef\xbb\xbf") else "") + result)
    return target, edits


# ---- measuring samples -------------------------------------------------------------------------------------------
SENTENCE_END = re.compile(r"[.!?…]+[\"'”’»)]*(?=\s|$)")


def measure(text):
    """Style statistics of a writing sample: sentence and paragraph lengths and punctuation habits."""
    text = text.replace("\r\n", "\n").strip()
    paragraphs = [p for p in re.split(r"\n[ \t]*\n", text) if p.strip()]
    sentences = []
    for p in paragraphs:
        sentences += [s for s in SENTENCE_END.split(p) if WORD.search(s)]
    words = len(re.findall(r"[^\W_]+(?:['’][^\W_]+)*", text))
    n_sent, n_par = max(len(sentences), 1), max(len(paragraphs), 1)
    per_k = (lambda count: round(count * 1000 / words, 1)) if words else (lambda count: 0.0)
    hyphens = len(re.findall(r"(?<=\s)-(?=\s)|(?<=\w)-(?=\w)", text))
    return {
        "words": words, "sentences": len(sentences), "paragraphs": len(paragraphs),
        "avg_sentence_words": round(words / n_sent, 1),
        "commas_per_sentence": round(text.count(",") / n_sent, 2),
        "avg_paragraph_sentences": round(len(sentences) / n_par, 1),
        "avg_paragraph_words": round(words / n_par, 1),
        "per_1000_words": {"!": per_k(text.count("!")), "?": per_k(text.count("?")), ":": per_k(text.count(":")),
                           "-": per_k(hyphens), ";": per_k(text.count(";"))},
    }


def describe(stats):
    """One line for a prompt."""
    marks = stats["per_1000_words"]
    return (f"average sentence {stats['avg_sentence_words']} words, {stats['commas_per_sentence']} commas per "
            f"sentence, paragraphs of about {stats['avg_paragraph_sentences']} sentences "
            f"({stats['avg_paragraph_words']} words); per 1000 words: {marks['!']} '!', {marks['?']} '?', "
            f"{marks[':']} ':', {marks['-']} '-'")


# ---- CLI ---------------------------------------------------------------------------------------------------------
def main(argv=None):
    parser = argparse.ArgumentParser(prog="humanize.py", description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("humanize", help="write a humanized copy of FILE to a new file")
    p.add_argument("file")
    p.add_argument("--typos", type=float, default=DEFAULT_TYPOS, help="percent of eligible words (default 0.2)")
    p.add_argument("--punct", type=float, default=DEFAULT_PUNCT, help="percent of eligible marks (default 2)")
    p.add_argument("--seed", type=int)
    p.add_argument("--out", help="new output file (must not exist); default '<name> humanized.<ext>'")
    sub.add_parser("measure", help="style statistics of a text file").add_argument("file")
    args = parser.parse_args(argv)
    try:
        if args.command == "measure":
            print(json.dumps(measure(Path(args.file).read_text(encoding="utf-8")), ensure_ascii=False))
            return OK
        target, edits = humanize_file(args.file, args.typos, args.punct, args.seed, args.out)
    except (OSError, ValueError) as error:
        print(f"humanize: {error}", file=sys.stderr)
        return ERROR
    print(f"humanize: wrote {target}")
    print(f"typos: {len(edits['words'])} of {edits['eligible_words']} eligible words ({args.typos:g}%); "
          f"punctuation: {len(edits['marks'])} of {edits['eligible_marks']} eligible marks deleted ({args.punct:g}%)"
          + (f"; seed {args.seed}" if args.seed is not None else ""))
    return OK


if __name__ == "__main__":
    sys.exit(main())
