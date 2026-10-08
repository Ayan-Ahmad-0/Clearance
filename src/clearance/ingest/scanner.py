"""Pattern scanner for prompt-injection text in documents and retrieved chunks.

Rule-based on purpose: cheap, explainable, runs at ingest. It catches templated
attacks and misses paraphrase and translation; scripts/run_injection_eval.py
measures both. Defence in depth, not the access control: the authorised-set filter
decides what can reach a prompt at all.
"""
import base64
import re
import unicodedata

ZERO_WIDTH_CHARS = "\u200b\u200c\u200d\u2060\ufeff"
_STRIP = {ord(c): None for c in ZERO_WIDTH_CHARS + "\u00ad"}
FLAGS = re.IGNORECASE | re.MULTILINE | re.DOTALL

PATTERNS = {
    "override": [
        (
            r"\b(ignore|disregard|forget|override)\b[^.]{0,40}\b(previous|prior|above|earlier|preceding)\b"
            r"[^.]{0,30}\b(instructions?|rules?|prompts?|context|guidelines?)\b"
        ),
        r"\bnew (instructions?|task)\s*:",
        r"\bignore (the|this|your) (question|user|request)\b",
    ],
    "role_spoof": [
        r"^\s*(system|assistant|developer)\s*:",
        r"</?\s*(system|assistant|developer)\s*>",
        r"\[/?(inst|sys)\]",
        r"\byou are now (a|an|the|in)\b",
    ],
    "exfiltration": [
        (
            r"\b(reveal|print|output|repeat|disclose|leak|list)\b[^.]{0,50}"
            r"\b(system prompt|verification codes?|secrets?|other documents|every other document"
            r"|all documents|full context|hidden (text|instructions))\b"
        ),
        r"\bregardless of (who|the user|permissions?|access)\b",
    ],
    "link_exfil": [
        r"!\[[^\]]*\]\(\s*https?://[^)\s]+\)",
        r"https?://\S*[?&](q|d|data|secret|answer|question)=",
    ],
    "delimiter_escape": [
        r"</?(context|documents?|chunks?|sources?|retrieved)>",
        r"\bend of (the )?(retrieved )?(context|documents?)\b",
        r"-{3,}\s*(end|begin)\b",
    ],
}
COMPILED = {cat: [re.compile(p, FLAGS) for p in pats] for cat, pats in PATTERNS.items()}
BASE64_RUN = re.compile(r"[A-Za-z0-9+/]{24,}={0,2}")


def normalise(text):
    """NFKC, then drop zero-width and soft-hyphen characters, so split words match."""
    return unicodedata.normalize("NFKC", text).translate(_STRIP)


def _categories(text):
    return {cat for cat, pats in COMPILED.items() if any(p.search(text) for p in pats)}


def scan(text):
    """Return {"flagged": bool, "categories": [...]}."""
    cats = _categories(normalise(text))
    if sum(ch in ZERO_WIDTH_CHARS for ch in text) >= 3:
        cats.add("zero_width")
    for blob in BASE64_RUN.findall(text):
        try:
            decoded = base64.b64decode(blob + "=" * (-len(blob) % 4), validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            continue
        if _categories(normalise(decoded)):
            cats.add("encoded")
    return {"flagged": bool(cats), "categories": sorted(cats)}