# LLMOps Platform – Week 4 Day 3: RAG service + /v1/ask with citations

```
curl ─► gateway /v1/ask ─┬─► rag-service /v1/retrieve   (hybrid BM25 + dense, in Kubernetes)
                         └─► Ollama on the Mac          (answers only from the retrieved sources, cites [1] [2])
```

Nothing in these steps deletes your files. The only things removed are a test container you
create in Step 3.

## What's new

| Piece | What it does |
|---|---|
| `rag/` (rag-service 0.1.0) | FastAPI service: `POST /v1/retrieve` returns the top chunks with source, scores and per-stage timings. Same method that scored 47/47 in the Copilot benchmark. Models are baked into the image. |
| gateway 0.4.0 | `POST /v1/ask` (JSON) and `POST /v1/ask/stream` (SSE): retrieve → grounded prompt → answer with `[n]` citations mapped back to files. Every answer is labelled `cited`, `uncited`, `invalid_citation` or `no_answer`. |
| `helm/rag-service` | Deployment + ClusterIP Service, startup probe for model loading, read-only root filesystem |
| `rag/eval/eval_service.py` | 47-question benchmark against the deployed service, with a `--min-top1` quality gate |
| `rag/eval/eval_ask.py` | End-to-end eval through the gateway: citation accuracy, invented citations, "I don't know" rate, latency |
| `DECISIONS.md` | D10–D14 |

## Step 0 – Start everything
Open **Ollama**, then Docker:
```bash
open -a Docker
until docker info >/dev/null 2>&1; do echo "waiting for Docker..."; sleep 5; done; echo ready
```

## Step 1 – Copy the Week 4 files in
```bash
cd ~/Downloads && unzip -o llmops-week4.zip
cp -R ~/Downloads/llmops-week4/. ~/Downloads/llmops-platform-week1/
cd ~/Downloads/llmops-platform-week1
git status
```
This adds `rag/` and `helm/rag-service/` and updates `gateway/main.py`, the gateway chart and `DECISIONS.md`.

## Step 2 – Build the RAG image (first time: ~5–10 minutes)
```bash
docker build -t rag-service:0.1.0 rag/
docker images rag-service
```
It downloads PyTorch (CPU) and both models once. Write down the image SIZE for your notes.

## Step 3 – Test the container on its own (and get your first real numbers)
```bash
docker run -d --name rag-test -p 8001:8001 rag-service:0.1.0
until curl -s localhost:8001/readyz >/dev/null; do sleep 2; done; curl -s localhost:8001/info; echo
python3 rag/eval/eval_service.py --min-top1 0.95
docker logs rag-test | head -3
docker rm -f rag-test        # removes only this test container
```
Expected: hybrid **47/47**, the same as the Copilot benchmark, and `PASS`.

## Step 4 – Cluster + images
```bash
docker build -t llm-gateway:0.4.0 gateway/
kind get clusters            # if "llmops" is missing: kind create cluster --config kind-config.yaml
kind load docker-image rag-service:0.1.0 --name llmops
kind load docker-image llm-gateway:0.4.0 --name llmops
```
The RAG image is large, so `kind load` takes a minute or two.

## Step 5 – Deploy the RAG service
```bash
helm upgrade --install rag-service helm/rag-service -n llmops --create-namespace --rollback-on-failure --timeout 5m
kubectl -n llmops get pods
kubectl -n llmops logs deploy/rag-service | head -5
```
Look for the `startup` log line: files 18, chunks 36, load_s.

## Step 6 – Upgrade the gateway to 0.4.0
```bash
helm upgrade --install llm-gateway helm/llm-gateway -n llmops --reset-then-reuse-values --rollback-on-failure --timeout 3m
export KEY=$(kubectl -n llmops get secret llm-gateway-secret -o jsonpath='{.data.API_KEYS}' | base64 -d)
curl -s localhost:8080/version; echo     # app_version 0.4.0, rag_url http://rag-service:8001
```
> New cluster (no gateway release yet)? Add `--set apiKeys="sk-$(openssl rand -hex 12)"` to the helm command.

## Step 7 – Ask questions
```bash
curl -s -X POST localhost:8080/v1/ask -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"question":"Why could a slow but healthy pod get restarted on a CPU-starved node?"}' | python3 -m json.tool
```
Check `answer`, `citations` (which files), `outcome`, `retrieve_ms` and `latency_s`.

Streaming (sources arrive first, then tokens, then the citations):
```bash
curl -N -X POST localhost:8080/v1/ask/stream -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"question":"What does the MAX_INFLIGHT limit control?"}'
```
A question the documents can't answer (we hope for "I don't know"):
```bash
curl -s -X POST localhost:8080/v1/ask -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"question":"What is the capital of France?"}' | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['outcome'], '|', d['answer'])"
```

## Step 8 – Benchmark the deployed service
Window 2:
```bash
kubectl -n llmops port-forward svc/rag-service 8001:8001
```
Window 1:
```bash
cd ~/Downloads/llmops-platform-week1
python3 rag/eval/eval_service.py --min-top1 0.95
```

## Step 9 – End-to-end eval through the gateway
```bash
python3 rag/eval/eval_ask.py --limit 10      # ~1 minute
python3 rag/eval/eval_ask.py                 # all 47, ~5 minutes
```
Paste the summary table to Claude. It is saved in `results/rag_ask_<time>.md`.

## Step 10 – Resources
```bash
kubectl -n llmops top pods
```

## Step 11 – Commit and push
```bash
git add rag helm gateway DECISIONS.md WEEK4_GUIDE.md results
git commit -m "Week 4: RAG retrieval service on Kubernetes, /v1/ask with streaming and verified citations"
git push
```

## If something goes wrong
| Symptom | Fix |
|---|---|
| rag pod `CrashLoopBackOff`, logs say "Read-only file system" | `helm upgrade rag-service helm/rag-service -n llmops --set readOnlyRootFilesystem=false` |
| rag pod `OOMKilled` | Docker Desktop → Settings → Resources → raise memory, or `--set config.loadReranker=false` |
| `/v1/ask` returns 503 "retrieval service unavailable" | `kubectl -n llmops get pods`: is rag-service Ready? |
| port 8001 already in use | the Step 3 container is still running: `docker rm -f rag-test` |
