"""Fail CI when a service's code version, Helm appVersion and image tag disagree.

Week 4 lesson: an image tagged 0.5.1 once ran 0.5.0 code, and only /version showed it.
Week 6 lesson: the chart set APP_VERSION, so 0.5.2 code reported 0.5.3 on the cluster. Charts no longer set it,
and image.tag may be left empty, which means "use the chart's appVersion" (the recommended setting).
This check catches a mismatch before anything is built.

    python ci/check_versions.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVICES = {
    "llm-gateway": ("gateway/main.py", "helm/llm-gateway"),
    "rag-service": ("rag/main.py", "helm/rag-service"),
}


def find(pattern: str, path: Path) -> str:
    m = re.search(pattern, path.read_text(encoding="utf-8"), re.MULTILINE)
    if not m:
        sys.exit(f"FAIL: pattern {pattern!r} not found in {path}")
    return m.group(1)


def main() -> int:
    failed = False
    for name, (code, chart) in SERVICES.items():
        versions = {
            f"{code} APP_VERSION": find(r'^APP_VERSION = (?:os\.getenv\("APP_VERSION", )?"([^"]+)"', ROOT / code),
            f"{chart}/Chart.yaml appVersion": find(r'^appVersion:\s*"?([^"\s#]+)', ROOT / chart / "Chart.yaml"),
        }
        tag = find(r'^\s+tag:\s*"?([^"\s#]*)', ROOT / chart / "values.yaml")
        versions[f"{chart}/values.yaml image.tag"] = tag or versions[f"{chart}/Chart.yaml appVersion"]
        ok = len(set(versions.values())) == 1
        failed |= not ok
        print(f"{'OK  ' if ok else 'FAIL'} {name}: " + ", ".join(f"{k} = {v}" for k, v in versions.items()))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
