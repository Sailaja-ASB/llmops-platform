# LLMOps Platform – Week 2: Kubernetes + Helm (with production reliability)

Goal: run the gateway on Kubernetes the way a platform team would, and prove it survives failures.

```
Mac (host)                         kind cluster "llmops" (namespace: llmops)
┌──────────────┐                   ┌──────────────────────────────────────────────┐
│ Ollama :11434│◄── host.docker ───│ Deployment llm-gateway (2 pods, 0.2)         │
│ qwen2.5:3b   │    .internal      │   probes: /healthz (liveness) /readyz (ready)│
└──────────────┘                   │   ConfigMap: model, prompt version, timeouts │
      ▲                            │   Secret: API keys                           │
      │ curl localhost:8080 ──────►│ Service NodePort 30080  (→ Mac port 8080)    │
                                   └──────────────────────────────────────────────┘
```

Ollama stays on the Mac (8 GB RAM is not enough to also run the model inside the cluster).

---

## What is new in the gateway (v0.2)

| Feature | How it works |
|---|---|
| `/version` | Shows app version, model, prompt version and breaker state |
| JSON logs + request IDs | Every request gets an `X-Request-ID` (or keeps yours) and one JSON log line |
| API keys | `X-API-Key` header required on `/v1/generate` when `API_KEYS` is set |
| Rate limit | Per key, per pod (default 60/min) → 429 |
| Request-size limit | Body over 32 KB → 413 |
| Timeouts | 3 s to connect, 120 s for generation → 504 if the model is too slow |
| Retries | Only when it **cannot connect** (2 retries, 0.2 s then 0.4 s). Slow generations are never retried |
| Circuit breaker | After 3 connection failures: fast 503 for 15 s, then one test request |
| New metrics | `llm_model_seconds`, `llm_gateway_overhead_seconds`, `llm_input_tokens_total`, `llm_breaker_rejections_total`, `llm_gateway_build_info` |
| `/healthz` | Only checks the gateway itself, **never Ollama** (so Kubernetes doesn't restart healthy pods during a model outage) |

All of this was tested locally against a mock model server (auth 401, validation 422, size 413, rate 429, breaker open → 503 → recovery 200).

---

## Step 0 – Install tools (once)

```bash
brew install kind kubectl helm
kind version && kubectl version --client && helm version
```

Docker Desktop → Settings → Resources: give Docker about **4 GB** of memory. Close extra Chrome tabs.

## Step 1 – Put the files in your repo

Copy into your existing `~/Downloads/llmops-platform-week1` folder:

```
llmops-platform-week1/
├── gateway/
│   ├── main.py          ← replace with v0.2
│   ├── Dockerfile       ← replace (graceful shutdown, fixed user id)
│   └── requirements.txt
├── helm/llm-gateway/    ← new: Chart.yaml, values.yaml, templates/
├── kind-config.yaml     ← new
├── benchmark.py         ← replace (adds --api-key)
├── WEEK2_GUIDE.md       ← this file
└── DECISIONS.md         ← new
```

## Step 2 – Test the new gateway locally first (5 min)

```bash
cd ~/Downloads/llmops-platform-week1/gateway
source .venv/bin/activate
API_KEYS=dev-key MODEL_NAME=qwen2.5:3b uvicorn main:app --port 8000
```

Second Terminal:

```bash
curl localhost:8000/version
curl -X POST localhost:8000/v1/generate -H "Content-Type: application/json" -d '{"prompt":"hi"}'          # 401
curl -X POST localhost:8000/v1/generate -H "X-API-Key: dev-key" -H "Content-Type: application/json" \
     -d '{"prompt":"What is Kubernetes in one sentence?","max_tokens":60}'                                  # 200
```

Look at the first Terminal: you'll see JSON log lines with `request_id`. Stop with Ctrl + C.

## Step 3 – Build the image and create the cluster

```bash
cd ~/Downloads/llmops-platform-week1
docker build -t llm-gateway:0.2 gateway/
kind create cluster --config kind-config.yaml          # takes 1–2 minutes
kind load docker-image llm-gateway:0.2 --name llmops   # kind can't see your local images otherwise
kubectl get nodes                                      # STATUS = Ready
```

## Step 4 – Test pod → Ollama connectivity FIRST

`localhost` inside a pod is the pod, not your Mac. Check before deploying:

```bash
kubectl run nettest --rm -it --image=curlimages/curl --restart=Never -- \
  curl -s http://host.docker.internal:11434/api/tags
```

- JSON with your models → works, continue.
- Fails → tell Claude. (Fallback: use your Mac's IP from `ipconfig getifaddr en0` and start Ollama with `OLLAMA_HOST=0.0.0.0 ollama serve`, only on a trusted home network. Never expose Ollama publicly.)

## Step 5 – Install with Helm

```bash
helm install llm-gateway helm/llm-gateway \
  --namespace llmops --create-namespace \
  --set apiKeys="sk-$(openssl rand -hex 12)"

kubectl -n llmops get pods -w        # wait for 2/2 pods READY 1/1, then Ctrl + C
kubectl -n llmops get secret llm-gateway-secret -o jsonpath='{.data.API_KEYS}' | base64 -d; echo   # your key
```

Save the key: `export KEY=<the key printed above>`

## Step 6 – Verify

```bash
curl localhost:8080/healthz
curl localhost:8080/version
curl -X POST localhost:8080/v1/generate -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
     -d '{"prompt":"Explain Helm in one sentence.","max_tokens":60}'
kubectl -n llmops logs -l app=llm-gateway --tail=5      # JSON logs from the pods
helm -n llmops list
```

Screenshot `kubectl get pods`, `/version` and one response: **deployment evidence**.

---

## Step 7 – Failure and recovery tests (the important part)

Run each, screenshot the result, write one line in `RUNBOOK.md`.

| # | Scenario | Do this | Expected |
|---|---|---|---|
| 1 | Invalid request | `curl -X POST localhost:8080/v1/generate -H "X-API-Key: $KEY" -H "Content-Type: application/json" -d '{"prompt":""}'` | 422, pod keeps running |
| 2 | No / wrong API key | same request without the header | 401 |
| 3 | Too large | send a 40 KB prompt | 413 |
| 4 | Ollama down | Quit Ollama (menu bar → Quit). Watch `kubectl -n llmops get pods -w` | Pods go **0/1 Ready** (not restarted, RESTARTS stays 0). Generate returns 503 fast. `/version` shows breaker `open` |
| 5 | Ollama back | Start Ollama again | Within ~15 s pods return to **1/1 Ready**, requests work |
| 6 | Pod crash | `kubectl -n llmops delete pod <one pod name>` | Kubernetes creates a new pod; the other pod keeps serving (no downtime) |
| 7 | Bad deploy + rollback | `helm upgrade llm-gateway helm/llm-gateway -n llmops --reuse-values --set image.tag=9.9` | New pod stuck in `ErrImagePull` / `ImagePullBackOff`; **old pods keep serving** (maxUnavailable 0) |
|  |  | `helm -n llmops history llm-gateway` then `helm -n llmops rollback llm-gateway 1` | Back to the working version |

For test 4, the reason pods are not restarted: `/healthz` doesn't check Ollama. That is the liveness vs readiness design from Week 1, now proven.

## Step 8 – Rolling update with versioning

Change the prompt version (simulates a new prompt release):

```bash
helm upgrade llm-gateway helm/llm-gateway -n llmops --reuse-values --set config.promptVersion=v2
kubectl -n llmops rollout status deployment/llm-gateway
curl localhost:8080/version            # prompt_version: v2
helm -n llmops history llm-gateway     # revisions 1, 2, 3…
```

The config checksum annotation makes pods restart automatically when the ConfigMap or Secret changes.

## Step 9 – Quick benchmark through Kubernetes

```bash
python3 benchmark.py --url http://localhost:8080 --api-key $KEY
```

Compare with Week 1. Also check gateway overhead alone:

```bash
curl -s localhost:8080/metrics | grep llm_gateway_overhead_seconds
```

## Step 10 – Commit and push

```bash
git add .
git commit -m "Week 2: Helm chart, probes, ConfigMap/Secret, API keys, JSON logs, breaker, rollback"
git push
```

## Cleanup (frees memory)

```bash
kind delete cluster --name llmops
```

---

## Evidence for the Week 2 article

- [ ] `kubectl get pods` with 2 pods Ready
- [ ] `/version` output
- [ ] Ollama down → pods 0/1 Ready, RESTARTS 0, 503 responses
- [ ] Ollama back → pods Ready again
- [ ] Deleted pod replaced with no downtime
- [ ] Bad image → old pods still serving → `helm rollback`
- [ ] JSON log line with a request ID
- [ ] Benchmark through Kubernetes vs Week 1

Article structure: problem → architecture → implementation → failure tests → what broke and how it recovered → lessons.

## Must-have vs stretch

- **Must-have:** Steps 3–8.
- **Stretch:** rate-limit test (set `config.rateLimitPerMin=3` and send 4 requests → 429), `RUNBOOK.md` with all 7 scenarios.
