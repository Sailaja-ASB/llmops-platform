"""Day 4: benchmark the LLM gateway.

Sends N requests to /v1/generate, then prints p50/p95 latency and tokens/sec
as a Markdown table you can paste into the README.
Uses only the Python standard library (nothing to install).

Usage:
    python3 benchmark.py                      # gateway on localhost:8001, 20 requests
    python3 benchmark.py --url http://localhost:8080 --api-key YOUR_KEY
"""
import argparse
import csv
import json
import math
import statistics
import time
import urllib.request

PROMPTS = [
    "What is Docker in one sentence?",
    "Explain RAG in two sentences.",
    "What is Kubernetes used for? One sentence.",
    "Give one tip for writing clean Python code.",
    "What does an API gateway do? One sentence.",
]


def percentile(values, p):
    """Nearest-rank percentile (simple and easy to explain in interviews)."""
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[rank - 1]


def call(url, prompt, max_tokens, api_key=""):
    body = json.dumps({"prompt": prompt, "max_tokens": max_tokens}).encode()
    req = urllib.request.Request(
        f"{url}/v1/generate", data=body, headers={"Content-Type": "application/json", **({"X-API-Key": api_key} if api_key else {})}
    )
    start = time.perf_counter()
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = json.loads(resp.read())
    data["client_latency_s"] = time.perf_counter() - start
    return data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8001")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--max-tokens", type=int, default=80)
    ap.add_argument("--api-key", default="", help="X-API-Key header (Week 2 gateway)")
    args = ap.parse_args()

    print(f"Warm-up request (loads the model, not counted)...")
    warm = call(args.url, "Say hello.", 10, args.api_key)
    print(f"  model = {warm.get('model')}, cold latency = {warm['client_latency_s']:.2f}s\n")

    rows = []
    for i in range(args.n):
        prompt = PROMPTS[i % len(PROMPTS)]
        r = call(args.url, prompt, args.max_tokens, args.api_key)
        rows.append(r)
        print(f"[{i + 1:>2}/{args.n}] {r['client_latency_s']:.2f}s  "
              f"{r.get('output_tokens', 0)} tokens  {r.get('tokens_per_s', 0):.1f} tok/s")

    lat = [r["client_latency_s"] for r in rows]
    tps = [r.get("tokens_per_s", 0) for r in rows]
    toks = [r.get("output_tokens", 0) for r in rows]

    with open("benchmark_results.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["request", "latency_s", "output_tokens", "tokens_per_s"])
        for i, r in enumerate(rows, 1):
            w.writerow([i, round(r["client_latency_s"], 3), r.get("output_tokens", 0),
                        r.get("tokens_per_s", 0)])

    print("\n## Benchmark results\n")
    print(f"Model: `{warm.get('model')}` | Requests: {args.n} (sequential) | "
          f"max_tokens: {args.max_tokens} | Cold start: {warm['client_latency_s']:.2f}s\n")
    print("| Metric | Value |")
    print("|---|---|")
    print(f"| p50 latency | {percentile(lat, 50):.2f} s |")
    print(f"| p95 latency | {percentile(lat, 95):.2f} s |")
    print(f"| Mean latency | {statistics.mean(lat):.2f} s |")
    print(f"| Mean throughput | {statistics.mean(tps):.1f} tokens/s |")
    print(f"| Mean output length | {statistics.mean(toks):.0f} tokens |")
    print("\nSaved every request to benchmark_results.csv")


if __name__ == "__main__":
    main()
