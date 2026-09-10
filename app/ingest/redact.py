"""Secret-shaped-string redaction.

This is the transmission-boundary enforcement point (plan §5): OCR text
produced from a screenshot passes through here BEFORE it is ever handed to
anything that calls the LiteLLM gateway (embedding, vision, synthesis). No
caller downstream of `redact_text` should see raw OCR output.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("email", re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")),
    ("phone", re.compile(r"(?<!\d)(?:\+?\d{1,3}[-.\s]?)?\(?\d{3,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}(?!\d)")),
    # AWS
    ("aws_access_key", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("aws_secret_key", re.compile(r"(?i)aws_secret_access_key\s*[=:]\s*\S+")),
    # generic bearer / api key style
    ("bearer_token", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-_.]{20,}")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("generic_kv_secret", re.compile(
        r"(?i)\b(api[_-]?key|secret|password|passwd|token|access[_-]?key)\b\s*[:=]\s*['\"]?[A-Za-z0-9/+_\-.]{8,}['\"]?"
    )),
    # CLI-flag-style secrets (space-separated, not key=value) - found live
    # 2026-09-10: `sshpass -p '1991984' ssh ...` sailed through every other
    # pattern (too short/low-entropy for the entropy pass, no `=`/`:` for
    # generic_kv_secret) and got sent to a hosted model. Scoped narrowly:
    # `-p` alone is too ambiguous across tools (port, pattern, preserve...)
    # to blanket-redact, but sshpass's `-p` is unambiguous, and long-form
    # --password/--passwd flags are safe to redact universally.
    ("sshpass_flag", re.compile(r"(?i)\bsshpass\s+-p\s*['\"]?[^\s'\"]{3,}['\"]?")),
    ("long_password_flag", re.compile(r"(?i)--pass(?:word|wd)?\b\s*=?\s*['\"]?[^\s'\"]{3,}['\"]?")),
    # user:password@host - curl/git-style embedded Basic Auth credentials
    ("basic_auth_url", re.compile(r"\b[A-Za-z0-9._%+-]+:[^\s@'\"/]{3,}@[A-Za-z0-9.-]+")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")),
    ("credit_card", re.compile(r"\b(?:\d[ -]*?){13,16}\b")),
    ("ssh_key_line", re.compile(r"\bssh-(rsa|ed25519|ecdsa)\s+[A-Za-z0-9+/=]{40,}")),
]

# High-entropy bare tokens (e.g. raw API keys with no "key=" prefix) get
# caught by a Shannon-entropy scan over whitespace-delimited chunks, since
# they won't match a named pattern above.
_ENTROPY_MIN_LEN = 20
_ENTROPY_THRESHOLD = 4.0
_TOKEN_RE = re.compile(r"[A-Za-z0-9+/_.\-]{%d,}" % _ENTROPY_MIN_LEN)


def _shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    counts: dict[str, int] = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    length = len(s)
    return -sum((c / length) * math.log2(c / length) for c in counts.values())


@dataclass
class RedactionResult:
    text: str
    redacted_count: int = 0
    categories: list[str] = field(default_factory=list)

    @property
    def had_redactions(self) -> bool:
        return self.redacted_count > 0


def redact_text(raw_text: str) -> RedactionResult:
    """Strip secret-shaped strings from OCR'd text.

    Returns the redacted text plus a count/category list so callers can log
    that a redaction happened without logging the secret itself.
    """
    text = raw_text
    total = 0
    categories: list[str] = []

    for name, pattern in _PATTERNS:
        text, n = pattern.subn(f"[REDACTED:{name.upper()}]", text)
        if n:
            total += n
            categories.append(name)

    # entropy pass on whatever tokens remain
    entropy_hits = 0

    def _entropy_sub(match: re.Match) -> str:
        nonlocal entropy_hits
        token = match.group(0)
        if _shannon_entropy(token) >= _ENTROPY_THRESHOLD:
            entropy_hits += 1
            return "[REDACTED:HIGH_ENTROPY]"
        return token

    text = _TOKEN_RE.sub(_entropy_sub, text)
    if entropy_hits:
        total += entropy_hits
        categories.append("high_entropy")

    return RedactionResult(text=text, redacted_count=total, categories=categories)


def assert_clean(text: str) -> None:
    """Defense-in-depth: raise if anything secret-shaped survived redaction.

    Call this immediately before any off-fleet network call (embedding,
    vision, synthesis) as a hard gate, not just a log line.
    """
    result = redact_text(text)
    if result.had_redactions:
        raise ValueError(
            f"refusing off-fleet call: {result.redacted_count} secret-shaped "
            f"string(s) still present after redaction ({result.categories}); "
            "this should never happen if redact_text() was already applied"
        )
