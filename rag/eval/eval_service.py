"""47-question retrieval benchmark against the deployed RAG service (over HTTP).

Checks that the service in Kubernetes ranks as well as the Copilot benchmark
(hybrid 47/47 Top-1), and measures latency as a client sees it.

    kubectl -n llmops port-forward svc/rag-service 8001:8001     # in another window
    python3 rag/eval/eval_service.py
    python3 rag/eval/eval_service.py --url http://localhost:8001 --min-top1 0.95

The gate (--min-top1) checks the mode the service actually ships with (its default mode, read
from /info), so a config change such as RETRIEVAL_MODE=dense is caught even when no code changed.
In GitHub Actions the table is also written to the job summary.
"""
import argparse
import json
import os
import statistics
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODES = ["dense", "bm25", "hybrid", "hybrid_rerank"]


def get(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read())


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
    ap.add_argument("--min-top1", type=float, default=None,
                    help="fail (exit 1) if Top-1 of the service's default mode is below this")
    args = ap.parse_args()

    questions = json.loads(Path(args.questions).read_text())
    total = len(questions)
    info = get(f"{args.url}/info")
    default_mode = info["default_mode"]
    modes = [m for m in MODES if m != "hybrid_rerank" or info.get("reranker_model")]  # reranker may be off
    post(f"{args.url}/v1/retrieve", {"question": "warm up", "mode": modes[-1]})

    lines = [f"## RAG service retrieval benchmark ({args.url})", "",
             f"Questions: {total} | ranking over top 10 chunks | client latency includes HTTP | "
             f"service {info['app_version']}, default mode **{default_mode}**",
             "", "| Mode | Top-1 | Hit@3 | MRR | server ms (avg) | client p50 ms | client p95 ms |",
             "|---|---|---|---|---|---|---|"]
    summary = {}
    for mode in modes:
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
        name = f"**{mode}** (default)" if mode == default_mode else mode
        lines.append(f"| {name} | {top1}/{total} ({100 * top1 / total:.1f}%) | {hit3}/{total} | {mrr:.3f} | "
                     f"{statistics.mean(server_ms):.1f} | {pct(client_ms, 50):.1f} | {pct(client_ms, 95):.1f} |")
    ok = True
    if args.min_top1 is not None:
        ok = summary[default_mode] >= args.min_top1
        lines += ["", f"{'PASS' if ok else 'FAIL'}: default mode {default_mode} Top-1 {summary[default_mode]:.1%} "
                      f"(minimum {args.min_top1:.0%})"]
    report = "\n".join(lines)
    print(report)
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(report + "\n")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
