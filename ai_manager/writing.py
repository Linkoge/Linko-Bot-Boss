"""Writing settings: the owner's style samples (English and Georgian kept apart) and the Humanizer's default rates.

Per profile: every instance keeps its own copy in its own database (Main does not push it, ai_manager/instance_sync.py),
so the owner of each instance writes their own samples. On save each sample is measured by the Humanizer script
(ai_manager/humanize.py: sentence and paragraph length, commas per sentence, ! ? : - frequency) and the statistics are
stored with it. Every Office and Humanizer task prompt carries the samples, their statistics and the punctuation habits
as `writing_style` (ai_manager/core.py), so the AI writes like the owner.
"""
import time

from . import humanize

KEY = "writing_style"
LANGUAGES = {"en": "English", "ka": "Georgian"}
SAMPLE_LIMIT = 6000
HARNESSES = frozenset({"office", "humanizer"})  # worker harnesses whose prompts get the owner's style
PUNCTUATION = ("Periods and question marks as normal. Fewer commas than typical AI text: split a long sentence instead "
               "of chaining clauses. Exclamation marks rare. Hyphens moderate. Colons occasional. No semicolons. "
               "Never the em dash or the en dash.")
DEFAULTS = {"samples": {"en": "", "ka": ""}, "stats": {"en": None, "ka": None},
            "typo_rate": humanize.DEFAULT_TYPOS, "punct_rate": humanize.DEFAULT_PUNCT, "updated_at": None}


def load(db):
    saved = db.setting(KEY) or {}
    value = {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS}} if isinstance(saved, dict) else dict(DEFAULTS)
    value["samples"] = {lang: str((value.get("samples") or {}).get(lang) or "") for lang in LANGUAGES}
    value["stats"] = {lang: (value.get("stats") or {}).get(lang) for lang in LANGUAGES}
    return value


def _rate(body, key, limit, name):
    value = body.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= limit:
        raise ValueError(f"{name} must be a number from 0 to {limit:g} (percent)")
    return round(float(value), 3)


def save(db, body):
    """Validate and store; each sample is measured now and its statistics kept with it."""
    if not isinstance(body, dict):
        raise ValueError("Send the writing settings")
    samples = body.get("samples") or {}
    if not isinstance(samples, dict) or samples.keys() - LANGUAGES.keys():
        raise ValueError("samples are kept per language: en (English) and ka (Georgian)")
    value = load(db)
    for lang in LANGUAGES:
        if lang in samples:
            text = samples[lang]
            if not isinstance(text, str) or len(text) > SAMPLE_LIMIT:
                raise ValueError(f"The {LANGUAGES[lang]} sample must be text of at most {SAMPLE_LIMIT} characters")
            text = text.replace("\r\n", "\n").strip()
            value["samples"][lang] = text
            value["stats"][lang] = humanize.measure(text) if text else None
    if "typo_rate" in body:
        value["typo_rate"] = _rate(body, "typo_rate", humanize.MAX_TYPOS, "The typo rate")
    if "punct_rate" in body:
        value["punct_rate"] = _rate(body, "punct_rate", humanize.MAX_PUNCT, "The punctuation rate")
    value["updated_at"] = time.time()
    db.set_setting(KEY, value)
    return value


def view(db):
    value = load(db)
    return {**value, "languages": LANGUAGES, "sample_limit": SAMPLE_LIMIT, "punctuation": PUNCTUATION,
            "limits": {"typo_rate": humanize.MAX_TYPOS, "punct_rate": humanize.MAX_PUNCT},
            "defaults": {"typo_rate": humanize.DEFAULT_TYPOS, "punct_rate": humanize.DEFAULT_PUNCT}}


def prompt(db):
    """The `writing_style` block of an Office or Humanizer task prompt."""
    value = load(db)
    block = {"instruction": ("Write like the owner. For each output use the sample and statistics of the output's "
                             "language (English or Georgian): match its sentence length, comma density, paragraph "
                             "length and punctuation habits, not its topic or wording. Without a sample for that "
                             "language, follow the punctuation habits alone."),
             "punctuation": PUNCTUATION,
             "humanizer_defaults": {"typos_percent": value["typo_rate"], "punct_percent": value["punct_rate"]}}
    samples = {}
    for lang, name in LANGUAGES.items():
        if text := value["samples"][lang]:
            stats = value["stats"][lang] or humanize.measure(text)
            samples[lang] = {"language": name, "sample": text, "stats": humanize.describe(stats)}
    if samples:
        block["samples"] = samples
    return block
