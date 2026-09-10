"""Reference-free-first RAG evaluation metrics (upgrade-roadmap "Now" item
6): faithfulness, answer relevancy, context precision, context recall - the
four classic RAGAS metrics, reimplemented natively against this project's
own LiteLLM gateway client (app.ingest.gateway) rather than depending on
the `ragas` package itself.

Why not the `ragas` package: tried it first (2026-09-10). `ragas==0.4.3`'s
own declared dependencies are mutually incompatible on PyPI right now -
its unconditional `from langchain_community.chat_models.vertexai import
ChatVertexAI` import (dead code for our purposes; we never touch VertexAI)
requires an old langchain-community, which drags langchain-core down to a
version langchain-openai (also a ragas dependency) refuses to run
against. Not version-pinnable within reasonable effort - a broken release
combination, not a local misconfiguration. Reimplementing these four
well-documented metrics directly is also a better architectural fit here
regardless: every LLM/embedding call in this project is required to route
through gateway.py's assert_clean() redaction gate (plan §5), and an eval
harness built on real recalled OCR context needs that guarantee exactly as
much as production code does - a generic library wouldn't know to do that.

Each function returns (score, details) - details is kept for the eval
report, not just the bare number, since a 0.4 faithfulness score is only
actionable if you can see *which* statement wasn't supported.
"""
from __future__ import annotations

import math
import time

import httpx

from app.ingest import gateway

# This harness fires many rapid, back-to-back judge/embed calls per gold-set
# question (one per extracted statement, one per retrieved context, etc.) -
# comfortably enough to trip the scoped LiteLLM key's 60rpm limit even for a
# handful of questions. Retry with backoff here rather than in gateway.py
# itself, since production call volume (one embed + one synthesize per
# request) never needed it - this is eval-specific traffic shape.
_MAX_RETRIES = 6


def _retry(fn, *args, **kwargs):
    for attempt in range(_MAX_RETRIES):
        try:
            return fn(*args, **kwargs)
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 429 or attempt == _MAX_RETRIES - 1:
                raise
            wait = float(e.response.headers.get("retry-after", 2 ** attempt))
            time.sleep(wait)
        except httpx.TransportError:
            # Connection refused/reset - seen intermittently against the
            # gateway under this harness's rapid-fire call volume, distinct
            # from a clean 429. Same backoff treatment.
            if attempt == _MAX_RETRIES - 1:
                raise
            time.sleep(2 ** attempt)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _extract_statements(text: str) -> list[str]:
    """Breaks a piece of text into atomic, standalone factual statements -
    one per line, no numbering/preamble requested so parsing stays simple."""
    prompt = (
        "Break the following text into a list of simple, standalone "
        "factual statements - one statement per line, no numbering, no "
        "preamble, no markdown. If a sentence makes multiple claims, split "
        "it into multiple lines. If the text makes no factual claims at "
        "all, output nothing.\n\nText:\n" + text
    )
    raw = _retry(gateway.synthesize, prompt, max_tokens=400)
    lines = [ln.strip(" -*\t") for ln in raw.splitlines()]
    return [ln for ln in lines if ln]


def _yes_no(prompt: str) -> bool:
    raw = _retry(gateway.synthesize, prompt, max_tokens=5).strip().lower()
    return raw.startswith("y")


def faithfulness(answer: str, contexts: list[str]) -> tuple[float, dict]:
    """Of the atomic statements in `answer`, what fraction are supported by
    `contexts`? Catches hallucination - claims the model made that aren't
    actually backed by what was retrieved."""
    context_text = "\n---\n".join(contexts) or "(no context retrieved)"
    statements = _extract_statements(answer)
    if not statements:
        return 1.0, {"statements": [], "note": "answer made no checkable factual claims"}

    verdicts = []
    for s in statements:
        supported = _yes_no(
            "Context:\n" + context_text + "\n\nStatement: " + s +
            "\n\nIs this statement directly supported by the context above? "
            "Answer with exactly one word: yes or no."
        )
        verdicts.append({"statement": s, "supported": supported})

    score = sum(1 for v in verdicts if v["supported"]) / len(verdicts)
    return score, {"statements": verdicts}


def answer_relevancy(question: str, answer: str, *, n: int = 3) -> tuple[float, dict]:
    """Generates n candidate questions the answer would be responding to,
    embeds each alongside the real question, and scores by mean cosine
    similarity. A generic/evasive answer produces generated questions that
    drift from what was actually asked - this is what that drift measures."""
    prompt = (
        f"Given only the answer below (not the original question), write "
        f"exactly {n} different questions that this answer would be a good "
        f"response to. One question per line, no numbering, no preamble.\n\n"
        f"Answer:\n{answer}"
    )
    raw = _retry(gateway.synthesize, prompt, max_tokens=200)
    generated = [ln.strip(" -*\t") for ln in raw.splitlines() if ln.strip(" -*\t")][:n]
    if not generated:
        return 0.0, {"generated_questions": [], "note": "no questions generated"}

    q_emb = _retry(gateway.embed, question)
    sims = []
    for gq in generated:
        gq_emb = _retry(gateway.embed, gq)
        sims.append(_cosine(q_emb, gq_emb))

    score = sum(sims) / len(sims)
    return score, {"generated_questions": list(zip(generated, sims))}


def context_precision(question: str, contexts: list[str], answer: str) -> tuple[float, dict]:
    """Average precision over the ranked retrieved contexts: for each
    context (in retrieval-rank order), judge whether it's actually useful
    for answering the question, then reward rankings where useful contexts
    land near the top. Judged against the generated answer (reference-free)
    rather than a ground-truth reference, per the roadmap's framing."""
    if not contexts:
        return 0.0, {"verdicts": []}

    verdicts = []
    for c in contexts:
        relevant = _yes_no(
            f"Question: {question}\n\nAnswer given: {answer}\n\n"
            f"Retrieved context: {c}\n\n"
            "Was this specific piece of retrieved context actually useful "
            "for producing that answer? Answer with exactly one word: yes or no."
        )
        verdicts.append(relevant)

    relevant_count = 0
    precision_sum = 0.0
    for k, v in enumerate(verdicts, start=1):
        if v:
            relevant_count += 1
            precision_sum += relevant_count / k

    score = precision_sum / relevant_count if relevant_count else 0.0
    return score, {"verdicts": list(zip(contexts, verdicts))}


def context_recall(reference: str, contexts: list[str]) -> tuple[float, dict]:
    """Of the atomic statements in the REFERENCE (ground-truth) answer, what
    fraction can be attributed to the retrieved contexts? Needs a reference
    - skip this metric for gold-set entries that don't have one, rather than
    guessing at ground truth."""
    context_text = "\n---\n".join(contexts) or "(no context retrieved)"
    statements = _extract_statements(reference)
    if not statements:
        return 1.0, {"statements": [], "note": "reference made no checkable factual claims"}

    verdicts = []
    for s in statements:
        attributable = _yes_no(
            "Context:\n" + context_text + "\n\nStatement: " + s +
            "\n\nCan this statement be attributed to (found in or directly "
            "inferable from) the context above? Answer with exactly one "
            "word: yes or no."
        )
        verdicts.append({"statement": s, "attributable": attributable})

    score = sum(1 for v in verdicts if v["attributable"]) / len(verdicts)
    return score, {"statements": verdicts}
