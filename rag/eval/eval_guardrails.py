"""Red-team eval for the input guardrail, through the gateway.

attacks: should be blocked (HTTP 400 blocked_by_guardrail). Includes variants that simple
         pattern matching is expected to MISS (other languages, misspellings, polite rephrasing),
         so the detection rate is honest, not 100% by construction.
benign:  real questions that look suspicious ("ignore", "API keys", "act as") and must NOT be blocked.

    python3 rag/eval/eval_guardrails.py
"""
import argparse
import json
import os
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent


def ask(url, key, question):
    body = json.dumps({"question": question, "max_tokens": 80}).encode()
    req = urllib.request.Request(f"{url}/v1/ask", data=body,
                                 headers={"Content-Type": "application/json", "X-API-Key": key})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8080")
    ap.add_argument("--key", default=os.getenv("KEY", ""))
    ap.add_argument("--out", default="results")
    args = ap.parse_args()
    data = json.loads((HERE / "redteam.json").read_text())

    by_cat = defaultdict(lambda: [0, 0])
    missed, lines = [], []
    for a in data["attacks"]:
        status, res = ask(args.url, args.key, a["text"])
        blocked = status == 400 and isinstance(res.get("detail"), dict)
        by_cat[a["category"]][0] += int(blocked)
        by_cat[a["category"]][1] += 1
        reason = res["detail"]["reason"] if blocked else f"HTTP {status}, outcome={res.get('outcome')}"
        print(f"{'BLOCKED' if blocked else 'MISSED '} [{a['category']}] {a['text'][:70]} -> {reason}")
        if not blocked:
            missed.append((a, res.get("answer", "")))

    false_pos = []
    for q in data["benign"]:
        status, res = ask(args.url, args.key, q)
        ok = status == 200
        print(f"{'ALLOWED' if ok else 'FALSE+ '} {q[:80]} -> HTTP {status}")
        if not ok:
            false_pos.append(q)

    n_att, n_ben = len(data["attacks"]), len(data["benign"])
    caught = n_att - len(missed)
    lines = [f"## Guardrail red-team eval ({datetime.now():%Y-%m-%d %H:%M})", "",
             "| Metric | Value |", "|---|---|",
             f"| Attacks blocked | {caught}/{n_att} ({100 * caught / n_att:.0f}%) |",
             f"| Benign questions wrongly blocked (false positives) | {len(false_pos)}/{n_ben} |", "",
             "| Attack category | Blocked |", "|---|---|"]
    lines += [f"| {c} | {b}/{t} |" for c, (b, t) in by_cat.items()]
    if missed:
        lines += ["", "### Missed attacks (what the model answered)", ""]
        lines += [f"- [{a['category']}] {a['text']}  \n  answer: {ans[:160]!r}" for a, ans in missed]
    if false_pos:
        lines += ["", "### False positives", ""] + [f"- {q}" for q in false_pos]
    report = "\n".join(lines)
    print("\n" + report)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"guardrails_redteam_{datetime.now():%Y%m%d_%H%M%S}.md"
    path.write_text(report + "\n")
    print(f"\nSaved {path}")


if __name__ == "__main__":
    main()
