"""RAG evaluation harness (upgrade-roadmap "Now" item 6). Runs the gold-set
questions in app/eval/gold_set.json through the live /query endpoint and
scores each with the four metrics in app/eval/metrics.py, then writes a
JSON report plus a plain-text summary to stdout.

Run manually after any change to retrieval (qdrant_store.search), the
prompt template (rag_service.query), or the embedding/synthesis models -
this is a regression check for answer quality, not something that needs to
run on a schedule like segment_sessions.py/retention_rollup.py.

    PYTHONPATH=. .venv/bin/python scripts/run_ragas_eval.py
"""
from __future__ import annotations

import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.config import settings
from app.eval import metrics

GOLD_SET_PATH = Path(__file__).parent.parent / "app" / "eval" / "gold_set.json"
REPORT_DIR = Path(__file__).parent.parent / "data"
RAG_BASE_URL = f"http://{settings.bind_host}:8089"


def run() -> dict:
    gold_set = json.loads(GOLD_SET_PATH.read_text())
    client = httpx.Client(base_url=RAG_BASE_URL, timeout=60.0)

    results = []
    for entry in gold_set:
        question = entry["question"]
        track_id = entry.get("track_id")
        reference = entry.get("reference")
        print(f"-> {question!r} (track={track_id})", file=sys.stderr)

        resp = client.post("/query", json={"question": question, "track_id": track_id})
        resp.raise_for_status()
        body = resp.json()
        answer = body["answer"]
        contexts = [s["ocr_text"] for s in body["sources"] if s.get("ocr_text")]

        faith_score, faith_detail = metrics.faithfulness(answer, contexts)
        rel_score, rel_detail = metrics.answer_relevancy(question, answer)
        prec_score, prec_detail = metrics.context_precision(question, contexts, answer)

        row = {
            "question": question,
            "track_id": track_id,
            "answer": answer,
            "num_contexts": len(contexts),
            "faithfulness": faith_score,
            "answer_relevancy": rel_score,
            "context_precision": prec_score,
            "details": {
                "faithfulness": faith_detail,
                "answer_relevancy": rel_detail,
                "context_precision": prec_detail,
            },
        }

        if reference:
            recall_score, recall_detail = metrics.context_recall(reference, contexts)
            row["context_recall"] = recall_score
            row["details"]["context_recall"] = recall_detail

        results.append(row)

    summary = {}
    for metric_name in ("faithfulness", "answer_relevancy", "context_precision", "context_recall"):
        values = [r[metric_name] for r in results if metric_name in r]
        if values:
            summary[metric_name] = {
                "mean": statistics.mean(values),
                "min": min(values),
                "n": len(values),
            }

    report = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "results": results,
    }

    REPORT_DIR.mkdir(exist_ok=True)
    out_path = REPORT_DIR / f"ragas_eval_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.json"
    out_path.write_text(json.dumps(report, indent=2, default=str))

    print("\n=== RAG eval summary ===", file=sys.stderr)
    for name, stats in summary.items():
        print(f"{name:20s} mean={stats['mean']:.2f}  min={stats['min']:.2f}  n={stats['n']}", file=sys.stderr)
    print(f"\nfull report: {out_path}", file=sys.stderr)

    return report


if __name__ == "__main__":
    run()
