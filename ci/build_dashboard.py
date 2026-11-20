"""Generates helm/observability/dashboards/llmops-overview.json (the LLMOps Grafana dashboard).

The dashboard is code: edit this file, not the JSON, then regenerate and validate:
    python3 ci/build_dashboard.py helm/observability/dashboards/llmops-overview.json
    python3 ci/check_dashboards.py        # every query must pass promtool (CI runs this too)
Writing panels in Python keeps 18 panels consistent (same datasource, 5-minute rate windows, units, layout).
"""
import json, sys
DS = {"type": "prometheus", "uid": "prometheus"}
panels, y = [], 0
pid = 0
def nid():
    global pid; pid += 1; return pid
def row(title):
    global y
    panels.append({"type": "row", "title": title, "id": nid(), "collapsed": False, "gridPos": {"h": 1, "w": 24, "x": 0, "y": y}, "panels": []}); y += 1
def panel(kind, title, targets, x, w, h=8, unit=None, desc="", stack=False, thresholds=None, decimals=None, min0=True):
    p = {"type": kind, "title": title, "id": nid(), "description": desc, "datasource": DS,
         "gridPos": {"h": h, "w": w, "x": x, "y": y},
         "targets": [{"datasource": DS, "expr": e, "legendFormat": l, "refId": chr(65 + i)} for i, (e, l) in enumerate(targets)],
         "fieldConfig": {"defaults": {}, "overrides": []}, "options": {}}
    d = p["fieldConfig"]["defaults"]
    if unit: d["unit"] = unit
    if decimals is not None: d["decimals"] = decimals
    if kind == "timeseries":
        d["custom"] = {"lineWidth": 2, "fillOpacity": 25 if stack else 8, "showPoints": "never",
                       "stacking": {"mode": "normal" if stack else "none"}}
        if min0: d["min"] = 0
        p["options"] = {"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi"}}
    if kind == "stat":
        p["options"] = {"reduceOptions": {"calcs": ["lastNotNull"]}, "colorMode": "value", "graphMode": "area", "textMode": "auto"}
    if thresholds:
        d["thresholds"] = {"mode": "absolute", "steps": thresholds}
        d["color"] = {"mode": "thresholds"}
    panels.append(p)
    return p
green, amber, red = "green", "#EAB839", "red"
W = "[5m]"

row("Is it up? Is it healthy?")
panel("stat", "Gateway pods up", [('sum(up{app="llm-gateway"})', "")], 0, 4, 5, desc="Gateway pods Prometheus can scrape right now.",
      thresholds=[{"color": red, "value": None}, {"color": amber, "value": 1}, {"color": green, "value": 2}])
panel("stat", "RAG pods up", [('sum(up{app="rag-service"})', "")], 4, 4, 5,
      thresholds=[{"color": red, "value": None}, {"color": green, "value": 1}])
API = 'route=~"/v1/.*"'
panel("stat", "API requests / s", [(f"sum(rate(llm_http_requests_total{{{API}}}{W}))", "")], 8, 4, 5, unit="reqps", decimals=2,
      desc="Every /v1 response, including requests rejected at the edge (401, 413, 429).")
panel("stat", "Error ratio (5m)", [(f'(sum(rate(llm_http_requests_total{{{API},code=~"5.."}}{W})) or vector(0)) / clamp_min(sum(rate(llm_http_requests_total{{{API}}}{W})), 1e-9)', "")],
      12, 4, 5, unit="percentunit", decimals=1, desc="5xx responses as a share of all /v1 responses. 4xx (bad key, rate limit, blocked attack) are the client's problem, not an outage.",
      thresholds=[{"color": green, "value": None}, {"color": amber, "value": 0.01}, {"color": red, "value": 0.05}])
panel("stat", "p95 latency (5m)", [(f"histogram_quantile(0.95, sum by (le) (rate(llm_request_latency_seconds_bucket{W})))", "")],
      16, 4, 5, unit="s", decimals=1, thresholds=[{"color": green, "value": None}, {"color": amber, "value": 10}, {"color": red, "value": 30}])
panel("stat", "Model cites its source (1h)", [('sum(increase(llm_rag_answers_total{outcome="cited"}[1h])) / clamp_min(sum(increase(llm_rag_answers_total[1h])), 1e-9)', "")],
      20, 4, 5, unit="percentunit", decimals=0, desc="Share of /v1/ask answers where the model cited valid sources itself (outcome=cited). The production version of the CI answer gate.",
      thresholds=[{"color": red, "value": None}, {"color": amber, "value": 0.8}, {"color": green, "value": 0.9}])
y += 5

row("Traffic and latency")
panel("timeseries", "API responses / s by status code", [(f"sum by (code) (rate(llm_http_requests_total{{{API}}}{W}))", "{{code}}")], 0, 12, unit="reqps", stack=True,
      desc="200 ok; 400 blocked by a guardrail; 401 no/bad API key; 413 too large; 422 invalid; 429 rate limited; 503 overloaded, breaker open or no model; 504 timeout.")
panel("timeseries", "End-to-end latency", [(f"histogram_quantile(0.50, sum by (le) (rate(llm_request_latency_seconds_bucket{W})))", "p50"),
                                            (f"histogram_quantile(0.95, sum by (le) (rate(llm_request_latency_seconds_bucket{W})))", "p95")], 12, 12, unit="s",
      desc="Aggregated across all gateway pods: sum the bucket rates first, then take the quantile (averaging per-pod p95s would be wrong).")
y += 8
panel("timeseries", "Time to first token (streaming)", [(f"histogram_quantile(0.50, sum by (le) (rate(llm_time_to_first_token_seconds_bucket{W})))", "p50"),
                                                         (f"histogram_quantile(0.95, sum by (le) (rate(llm_time_to_first_token_seconds_bucket{W})))", "p95")], 0, 8, unit="s")
panel("timeseries", "Where the time goes (p95)", [(f"histogram_quantile(0.95, sum by (le) (rate(llm_model_seconds_bucket{W})))", "inside the model"),
                                                   (f"histogram_quantile(0.95, sum by (le) (rate(llm_gateway_overhead_seconds_bucket{W})))", "gateway overhead"),
                                                   (f"histogram_quantile(0.95, sum by (le) (rate(llm_rag_retrieve_seconds_bucket{W})))", "RAG retrieval")], 8, 8, unit="s")
panel("timeseries", "Generation speed (p50)", [(f"histogram_quantile(0.50, sum by (le) (rate(llm_tokens_per_second_bucket{W})))", "tokens / s")], 16, 8, unit="none")
y += 8

row("Capacity and protection")
panel("timeseries", "In-flight generations per pod", [("sum by (pod) (llm_inflight_requests)", "{{pod}}")], 0, 8, unit="none",
      desc="Generations running right now. One Ollama instance serves them all, so more pods do not add model capacity.")
panel("timeseries", "Load shed and circuit breaker", [(f"sum(rate(llm_load_shed_total{W}))", "shed (pod at MAX_INFLIGHT)"),
                                                       (f"sum(rate(llm_breaker_rejections_total{W}))", "breaker open")], 8, 8, unit="reqps")
panel("timeseries", "Tokens / s across the platform", [(f"sum(rate(llm_input_tokens_total{W}))", "prompt tokens"),
                                                        (f"sum(rate(llm_output_tokens_total{W}))", "generated tokens")], 16, 8, unit="none")
y += 8

row("RAG answers and guardrails")
panel("timeseries", "Answers by citation outcome", [(f"sum by (outcome) (rate(llm_rag_answers_total{W}))", "{{outcome}}")], 0, 8, unit="reqps", stack=True,
      desc="cited = model cited valid sources; cited_repaired = the gateway attached a citation; uncited / invalid_citation / no_answer = worth a look.")
panel("timeseries", "Guardrail actions", [(f"sum by (stage, reason) (rate(llm_guardrail_blocks_total{W}))", "{{stage}}: {{reason}}")], 8, 8, unit="reqps", stack=True)
panel("timeseries", "RAG retrieval time (p95, service side)", [(f"histogram_quantile(0.95, sum by (le, mode) (rate(rag_retrieve_seconds_bucket{W})))", "{{mode}}")], 16, 8, unit="s")
y += 8

row("What is deployed")
# One row per service, with a "service" column; only the labels that matter (the RAG prompt version, not the
# plain-generate one). Week 6: the first version showed prompt_version "v1" and two noisy "Value #A/#B" columns.
t = panel("table", "Versions running now",
          [('max by (service, app_version, model, rag_prompt_version) (label_replace(llm_gateway_build_info, "service", "llm-gateway", "", ""))', "gateway"),
           ('max by (service, app_version, embedding_model, default_mode) (label_replace(rag_service_build_info, "service", "rag-service", "", ""))', "rag-service")],
          0, 24, 6, desc="Read from the *_build_info metrics each pod exposes: what is actually running, not what the chart says.")
for tg in t["targets"]: tg["format"] = "table"; tg["instant"] = True
t["transformations"] = [{"id": "merge", "options": {}},
                        {"id": "organize", "options": {
                            "excludeByName": {"Time": True, "Value": True, "Value #A": True, "Value #B": True},
                            "indexByName": {"service": 0, "app_version": 1, "model": 2, "rag_prompt_version": 3,
                                            "embedding_model": 4, "default_mode": 5},
                            "renameByName": {"app_version": "version", "rag_prompt_version": "RAG prompt",
                                             "embedding_model": "embedding model", "default_mode": "retrieval mode"}}}]

dash = {"uid": "llmops-overview", "title": "LLMOps platform overview", "tags": ["llmops"], "timezone": "browser",
        "schemaVersion": 39, "version": 1, "editable": False, "refresh": "30s", "time": {"from": "now-1h", "to": "now"},
        "panels": panels, "templating": {"list": []}, "annotations": {"list": []}}
json.dump(dash, open(sys.argv[1], "w"), indent=2)
print(len([p for p in panels if p["type"] != "row"]), "panels")
