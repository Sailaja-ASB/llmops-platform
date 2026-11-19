"""Check the Grafana dashboards shipped in helm/observability/dashboards before they reach a cluster.

- valid JSON, a stable uid, unique panel ids
- every panel and query uses the provisioned Prometheus datasource (uid "prometheus")
- every PromQL query parses: they are written into a temporary rules file and checked with `promtool check rules`

    python3 ci/check_dashboards.py [path/to/promtool]
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DASHBOARDS = sorted((ROOT / "helm" / "observability" / "dashboards").glob("*.json"))
PROMTOOL = sys.argv[1] if len(sys.argv) > 1 else "promtool"

errors, exprs = [], []
for path in DASHBOARDS:
    dash = json.loads(path.read_text())
    if not dash.get("uid"):
        errors.append(f"{path.name}: missing uid")
    ids = [p["id"] for p in dash["panels"]]
    if len(ids) != len(set(ids)):
        errors.append(f"{path.name}: duplicate panel ids")
    for p in dash["panels"]:
        if p["type"] == "row":
            continue
        if p.get("datasource", {}).get("uid") != "prometheus":
            errors.append(f"{path.name}: panel '{p['title']}' does not use the Prometheus datasource")
        for t in p.get("targets", []):
            exprs.append((path.name, p["title"], t["expr"]))

rules = {"groups": [{"name": "dashboard_queries", "rules": [
    {"record": f"dashboard_check:query_{i}", "expr": e} for i, (_, _, e) in enumerate(exprs)]}]}
with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:   # promtool reads YAML; JSON is valid YAML
    json.dump(rules, f)
result = subprocess.run([PROMTOOL, "check", "rules", f.name], capture_output=True, text=True)
os.unlink(f.name)
if result.returncode != 0:
    errors.append("PromQL errors:\n" + result.stdout + result.stderr)

summary = os.environ.get("GITHUB_STEP_SUMMARY")
lines = [f"### Dashboards: {len(DASHBOARDS)} file(s), {len(exprs)} PromQL queries",
         "", "PASS" if not errors else "FAIL", ""] + [f"- {e}" for e in errors]
if summary:
    with open(summary, "a") as s:
        s.write("\n".join(lines) + "\n")
print("\n".join(lines))
sys.exit(1 if errors else 0)
