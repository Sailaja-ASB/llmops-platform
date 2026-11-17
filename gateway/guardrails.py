"""Guardrails for the RAG endpoint (Week 4 Day 4). Pure functions, no network, easy to unit test.

Input:   detect_injection()      block prompt-injection attempts in the question
Context: filter_chunks()         drop retrieved chunks that contain injection text (indirect injection)
Output:  parse_citations()       map [n] markers back to retrieved chunks, find invented numbers
         repair_citations()      attach a citation to uncited sentences when one chunk clearly supports them
         strip_invalid_citations() remove [n] markers that point to nothing
         redact()                remove API keys, tokens and email addresses from answers
"""
from __future__ import annotations

import re

NO_ANSWER = "I don't know based on the provided documents."

# ---------- Prompt injection ----------
_INJECTION_PATTERNS = [
    ("ignore_instructions", r"\b(ignore|disregard|forget|override|bypass)\b[^.?!\n]{0,40}\b(previous|prior|above|earlier|all|your|system|the)\b[^.?!\n]{0,25}\b(instructions?|prompts?|rules?|guidelines?|context)\b"),
    ("reveal_prompt", r"\b(reveal|show|print|repeat|output|display|tell me|leak)\b[^.?!\n]{0,30}\b(system prompt|hidden prompt|initial prompt|your instructions|your prompt)\b"),
    ("role_override", r"\b(you are now|from now on,? you are|act as|pretend to be|roleplay as)\b[^.?!\n]{0,40}\b(dan|unrestricted|jailbroken|no rules|without (any )?restrictions|developer mode)\b"),
    ("jailbreak_mode", r"\b(developer mode|jailbreak mode|dan mode|do anything now)\b"),
    ("fake_system_tag", r"(<\s*/?\s*system\s*>|\[\s*system\s*\]|###\s*system|<\|im_start\|>)"),
    ("exfiltrate_secrets", r"\b(print|show|reveal|list|output|give me)\b[^.?!\n]{0,30}\b(api[ _-]?keys?|secrets?|passwords?|environment variables|env vars|credentials)\b"),
]
_COMPILED = [(name, re.compile(rx, re.IGNORECASE)) for name, rx in _INJECTION_PATTERNS]
# Text inside quotes is being *talked about*, not used as a command:
# "What is it called when a message says 'ignore all previous instructions'?" must not be blocked.
_QUOTED = re.compile(r"\"[^\"]{3,300}\"|'[^']{3,300}'|“[^”]{3,300}”|‘[^’]{3,300}’")


def detect_injection(text: str, ignore_quoted: bool = True) -> str | None:
    """Return the name of the first matching attack pattern, or None if the text looks safe."""
    scan = _QUOTED.sub(" ", text) if ignore_quoted else text
    for name, rx in _COMPILED:
        if rx.search(scan):
            return name
    return None


def filter_chunks(chunks: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split retrieved chunks into (safe, blocked).

    Quoted text is exempt here too: without it, the knowledge-base document that *explains*
    prompt injection (and quotes an example attack) was dropped for exactly the question it
    answers. Trade-off: an attacker who wraps a command in quotes passes this layer, which is
    why the prompt also tells the model never to follow instructions inside sources.
    """
    safe, blocked = [], []
    for c in chunks:
        reason = detect_injection(c["text"])
        (blocked if reason else safe).append({**c, "blocked_reason": reason} if reason else c)
    return safe, blocked


# ---------- Citations ----------
_CITE_RE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")
# A sentence ends at . ! ? (plus any citations right after it) followed by whitespace or the end,
# so "ops@example.com", "3.5" and "v1.2" are never split.
_BOUNDARY = re.compile(r"[.!?](?:\s*\[\d+(?:\s*,\s*\d+)*\])*(?=\s|$)")
_WORD_RE = re.compile(r"[a-z0-9]+(?:[_\-.][a-z0-9]+)*")
_STOP = set("""a an the and or but if then so of to in on at by for with from as is are was were be been being it its
this that these those there here which who whom what when where why how can could should would may might will
do does did not no yes you your we our they their he she his her them than also into over under about such only
very more most less much many each every any some all both either neither one two using use used""".split())


def parse_citations(answer: str, chunks: list[dict]) -> tuple[list[int], list[int]]:
    """Return (valid, invalid) citation numbers in order of first appearance."""
    numbers: list[int] = []
    for group in _CITE_RE.findall(answer):
        for n in group.split(","):
            n = int(n.strip())
            if n not in numbers:
                numbers.append(n)
    valid = [n for n in numbers if 1 <= n <= len(chunks)]
    return valid, [n for n in numbers if n not in valid]


def strip_invalid_citations(answer: str, invalid: list[int]) -> str:
    """Remove citation numbers that point to no retrieved chunk; drop brackets left empty."""
    if not invalid:
        return answer

    def fix(m):
        keep = [n.strip() for n in m.group(1).split(",") if int(n) not in invalid]
        return f"[{', '.join(keep)}]" if keep else ""

    return re.sub(r"\s+([.,;:!?])", r"\1", _CITE_RE.sub(fix, answer)).strip()


def _content_words(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall(text.lower()) if w not in _STOP and len(w) > 2}


def split_sentences(text: str) -> list[str]:
    """Split into pieces that join back to exactly the original text (whitespace kept)."""
    pieces, start = [], 0
    for m in _BOUNDARY.finditer(text):
        pieces.append(text[start:m.end()])
        start = m.end()
    if start < len(text):
        pieces.append(text[start:])
    return pieces


def repair_citations(answer: str, chunks: list[dict], min_overlap: float = 0.5) -> tuple[str, list[int]]:
    """Add [n] to sentences that have no citation when one chunk contains at least `min_overlap`
    of the sentence's content words. Returns (answer, numbers added). Only uncited sentences are
    touched; sentences that no chunk supports stay uncited, so unsupported claims remain visible."""
    added: list[int] = []
    out = []
    for piece in split_sentences(answer):
        words = _content_words(piece)
        if _CITE_RE.search(piece) or len(words) < 2:
            out.append(piece)
            continue
        best_n, best = 0, 0.0
        for n, c in enumerate(chunks, start=1):
            overlap = len(words & _content_words(c["text"])) / len(words)
            if overlap > best:
                best_n, best = n, overlap
        if best_n and best >= min_overlap:
            body = piece.rstrip()
            trail = piece[len(body):]
            end = body[-1] if body[-1] in ".!?" else ""
            out.append(f"{body[:-1] if end else body} [{best_n}]{end}{trail}")
            if best_n not in added:
                added.append(best_n)
        else:
            out.append(piece)
    return "".join(out), added


def is_no_answer(answer: str) -> bool:
    return NO_ANSWER.lower().rstrip(".") in answer.lower()


# ---------- Output redaction ----------
_REDACT = [
    ("api_key", re.compile(r"\b(sk|pk|rk)-[A-Za-z0-9_\-]{12,}\b")),
    ("aws_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{16,}\b")),
    ("email", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
]


def redact(text: str) -> tuple[str, list[str]]:
    found = []
    for name, rx in _REDACT:
        if rx.search(text):
            found.append(name)
            text = rx.sub(f"[REDACTED {name.upper()}]", text)
    return text, found


def uncited_sentences(text: str) -> int:
    """Sentences with real content but no [n]: claims the user cannot check against a source."""
    if is_no_answer(text):
        return 0
    return sum(1 for s in split_sentences(text)
               if len(_content_words(s)) >= 3 and not _CITE_RE.search(s))
