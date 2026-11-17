"""Turn Locust CSV output into a Markdown table (p50 / p95 / p99, error rate).

Usage:
    python3 loadtest/report.py results/steps            # reads results/steps_stats.csv
    python3 loadtest/report.py results/steps --history  # also prints the per-stage view
"""
import csv
import sys
from collections import OrderedDict


def seconds(ms):
    try:
        return f"{float(ms) / 1000:.2f} s"
    except (TypeError, ValueError):
        return "-"


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    prefix = sys.argv[1]

    with open(f"{prefix}_stats.csv") as f:
        rows = {r["Name"]: r for r in csv.DictReader(f)}

    opened = rows.get("stream open", {})
    total = int(opened.get("Request Count", 0) or 0)
    failed = int(opened.get("Failure Count", 0) or 0)
    error_rate = (failed / total * 100) if total else 0.0

    print(f"## Load test: {prefix}\n")
    print(f"Requests: {total} | Failed: {failed} | Error rate: {error_rate:.1f}% | "
          f"Throughput: {float(opened.get('Requests/s', 0) or 0):.2f} req/s\n")
    print("| Metric | p50 | p95 | p99 | max |")
    print("|---|---|---|---|---|")
    for name in ("TTFT", "full response"):
        r = rows.get(name)
        if r:
            print(f"| {name} | {seconds(r['50%'])} | {seconds(r['95%'])} | "
                  f"{seconds(r['99%'])} | {seconds(r['Max Response Time'])} |")

    if "--history" in sys.argv:
        # Last row per user count from the stats history = how latency changed as load grew.
        # Needs locust --csv-full-history so per-name rows are written.
        latest = {"TTFT": OrderedDict(), "full response": OrderedDict(), "stream open": OrderedDict()}
        with open(f"{prefix}_stats_history.csv") as f:
            for r in csv.DictReader(f):
                if r["Name"] in latest and r["User Count"] not in ("0", ""):
                    latest[r["Name"]][int(r["User Count"])] = r
        users_seen = list(latest["full response"].keys())
        if users_seen:
            print("\n### By number of users (rolling window at the end of each stage)\n")
            print("| Users | TTFT p50 | TTFT p95 | Full p50 | Full p95 | Failures/s |")
            print("|---|---|---|---|---|---|")
            for users in users_seen:
                t = latest["TTFT"].get(users, {})
                fr = latest["full response"][users]
                so = latest["stream open"].get(users, {})
                print(f"| {users} | {seconds(t.get('50%'))} | {seconds(t.get('95%'))} | "
                      f"{seconds(fr['50%'])} | {seconds(fr['95%'])} | "
                      f"{float(so.get('Failures/s') or 0):.2f} |")
        else:
            print("\n(no per-stage history: run locust with --csv-full-history)")


if __name__ == "__main__":
    main()
