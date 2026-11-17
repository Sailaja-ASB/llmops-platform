"""End-to-end RAG eval through the gateway: retrieval + LLM answer + citations.

For each benchmark question, call POST /v1/ask and check:
  - retrieval hit:       the expected source is among the retrieved chunks
  - cited:               the answer contains at least one valid [n] citation
  - citation accuracy:   a cited chunk comes from the expected source file
  - invalid citations:   the answer cites a number that was never retrieved (invented source)
  - "I don't know":      the model refused although the answer was in the documents
  - repaired:            the model gave no citation, the gateway attached one (Day 4 guardrail)
Model behaviour (what the LLM wrote) and final output (after guardrails) are reported separately.

    export KEY=...   # gateway API key
    python3 rag/eval/eval_ask.py --limit 10                          # deployed prompt, quick check
    python3 rag/eval/eval_ask.py --prompt-version rag-v1             # all 47 with the Day 3 prompt
    python3 rag/eval/eval_ask.py --prompt-version rag-v2             # all 47 with the Day 4 prompt
"""
import argparse
import json
import os
import statistics
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent


def pct(values, p):
    values = sorted(values)
    return values[min(len(values) - 1, int(round(p / 100 * (len(values) - 1))))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8080")
    ap.add_argument("--key", default=os.getenv("KEY", ""))
    ap.add_argument("--questions", default=str(HERE / "questions_47.json"))
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--prompt-version", choices=["rag-v1", "rag-v2"], default=None,
                    help="override the deployed prompt (A/B comparison)")
    ap.add_argument("--out", default="results")
    args = ap.parse_args()

    questions = json.loads(Path(args.questions).read_text())[: args.limit]
    rows = []
    for i, q in enumerate(questions, start=1):
        payload = {"question": q["question"], "max_tokens": 160}
        if args.prompt_version:
            payload["prompt_version"] = args.prompt_version
        body = json.dumps(payload).encode()
        req = urllib.request.Request(f"{args.url}/v1/ask", data=body,
                                     headers={"Content-Type": "application/json", "X-API-Key": args.key})
        t = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                res = json.loads(r.read())
        except urllib.error.HTTPError as exc:
            rows.append({"id": q["id"], "error": f"HTTP {exc.code}"})
            print(f"[{i}/{len(questions)}] {q['id']} HTTP {exc.code}")
            continue
        wall = time.perf_counter() - t
        retrieved = [s["source"] for s in res["sources"]]
        cited = [c["source"] for c in res["citations"]]
        model_cited = [c["source"] for c in res["citations"] if not c.get("repaired")]
        row = {"id": q["id"], "type": q["type"], "question": q["question"], "expected": q["expected_source"],
               "prompt_version": res.get("prompt_version"),
               "outcome": res["outcome"], "retrieval_hit": q["expected_source"] in retrieved,
               "citation_correct": q["expected_source"] in cited,
               "model_citation_correct": q["expected_source"] in model_cited,
               "uncited_sentences": res.get("uncited_sentences", 0),
               "invalid_citations": res["invalid_citations"],
               "retrieve_ms": res["retrieve_ms"], "latency_s": round(wall, 2), "answer": res["answer"]}
        rows.append(row)
        mark = "OK " if row["citation_correct"] else "-- "
        print(f"[{i}/{len(questions)}] {mark}{q['id']} {res['outcome']:16} cited={cited} "
              f"expected={q['expected_source']} {wall:.1f}s")

    ok = [r for r in rows if "error" not in r]
    n = len(ok)
    outcomes = Counter(r["outcome"] for r in ok)
    lat = [r["latency_s"] for r in ok]
    version = ok[0]["prompt_version"] if ok else args.prompt_version
    lines = [
        f"## RAG answer eval through the gateway ({datetime.now():%Y-%m-%d %H:%M}, prompt {version})", "",
        f"Questions: {len(rows)} | answered: {n} | HTTP errors: {len(rows) - n}", "",
        "| Metric | Value |", "|---|---|",
        f"| Retrieval hit (expected source retrieved, top 3) | {sum(r['retrieval_hit'] for r in ok)}/{n} |",
        "| **Model behaviour** | |",
        f"| Model cited a valid source | {outcomes['cited']}/{n} |",
        f"| Model cited the expected source | {sum(r['model_citation_correct'] for r in ok)}/{n} |",
        f"| Model invented a citation number | {outcomes['invalid_citation']}/{n} |",
        f"| Model gave no citation | {outcomes['uncited'] + outcomes['cited_repaired']}/{n} |",
        f"| \"I don't know\" (answer was in the documents) | {outcomes['no_answer']}/{n} |",
        "| **Final output (after guardrails)** | |",
        f"| Citation repaired by the gateway | {outcomes['cited_repaired']}/{n} |",
        f"| Final citation accuracy (cites the expected source) | {sum(r['citation_correct'] for r in ok)}/{n} |",
        f"| Sentences still without a citation (total) | {sum(r['uncited_sentences'] for r in ok)} |",
        "| **Latency** | |",
        f"| Retrieval time (avg, via gateway) | {statistics.mean(r['retrieve_ms'] for r in ok):.1f} ms |" if ok else "",
        f"| End-to-end latency p50 / p95 | {pct(lat, 50):.1f} s / {pct(lat, 95):.1f} s |" if ok else "",
    ]
    misses = [r for r in ok if not r["citation_correct"]]
    if misses:
        lines += ["", f"### Not citing the expected source ({len(misses)})", ""]
        lines += [f"- {r['id']} [{r['type']}] {r['outcome']}, retrieval_hit={r['retrieval_hit']}: "
                  f"{r['answer'][:140]!r}" for r in misses]
    report = "\n".join(lines)
    print("\n" + report)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"rag_ask_{version}_{stamp}"
    (out / f"{name}.json").write_text(json.dumps(rows, indent=2))
    (out / f"{name}.md").write_text(report + "\n")
    print(f"\nSaved {out}/{name}.md")


if __name__ == "__main__":
    main()
