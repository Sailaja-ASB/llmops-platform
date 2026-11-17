"""47-question retrieval benchmark against the deployed RAG service (over HTTP).

Checks that the service in Kubernetes ranks as well as the Copilot benchmark
(hybrid 47/47 Top-1), and measures latency as a client sees it.

    kubectl -n llmops port-forward svc/rag-service 8001:8001     # in another window
    python3 rag/eval/eval_service.py
    python3 rag/eval/eval_service.py --url http://localhost:8001 --min-top1 0.95
"""
import argparse
import json
import statistics
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODES = ["dense", "bm25", "hybrid", "hybrid_rerank"]


def post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def pct(values, p):
    values = sorted(values)
    return values[min(len(values) - 1, int(round(p / 100 * (len(values) - 1))))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8001")
    ap.add_argument("--questions", default=str(HERE / "questions_47.json"))
    ap.add_argument("--min-top1", type=float, default=None, help="fail (exit 1) if hybrid Top-1 is below this")
    args = ap.parse_args()

    questions = json.loads(Path(args.questions).read_text())
    total = len(questions)
    post(f"{args.url}/v1/retrieve", {"question": "warm up", "mode": "hybrid_rerank"})

    lines = [f"## RAG service retrieval benchmark ({args.url})", "",
             f"Questions: {total} | ranking over top 10 chunks | client latency includes HTTP",
             "", "| Mode | Top-1 | Hit@3 | MRR | server ms (avg) | client p50 ms | client p95 ms |",
             "|---|---|---|---|---|---|---|"]
    summary = {}
    for mode in MODES:
        ranks, server_ms, client_ms = [], [], []
        for q in questions:
            t = time.perf_counter()
            res = post(f"{args.url}/v1/retrieve", {"question": q["question"], "top_k": 10, "mode": mode})
            client_ms.append((time.perf_counter() - t) * 1000)
            server_ms.append(res["timings_ms"]["total_ms"])
            sources = list(dict.fromkeys(c["source"] for c in res["chunks"]))
            ranks.append(sources.index(q["expected_source"]) + 1 if q["expected_source"] in sources else None)
        top1 = sum(r == 1 for r in ranks)
        hit3 = sum(1 for r in ranks if r and r <= 3)
        mrr = sum(1 / r for r in ranks if r) / total
        summary[mode] = top1 / total
        lines.append(f"| {mode} | {top1}/{total} ({100 * top1 / total:.1f}%) | {hit3}/{total} | {mrr:.3f} | "
                     f"{statistics.mean(server_ms):.1f} | {pct(client_ms, 50):.1f} | {pct(client_ms, 95):.1f} |")
    print("\n".join(lines))

    if args.min_top1 is not None:
        ok = summary["hybrid"] >= args.min_top1
        print(f"\n{'PASS' if ok else 'FAIL'}: hybrid Top-1 {summary['hybrid']:.1%} (minimum {args.min_top1:.0%})")
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
