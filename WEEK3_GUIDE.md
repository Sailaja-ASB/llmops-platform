# LLMOps Platform – Week 3: Streaming, Load Testing & Autoscaling

Goal: find out what happens with **many users at once**, measure what users actually feel
(time to first token), and test whether autoscaling helps.

**Hypothesis to test this week:** Ollama on the Mac generates one request at a time
(`OLLAMA_NUM_PARALLEL=1`, you saw it in the `ollama serve` log). So with more users,
requests **queue at Ollama**. Adding gateway pods (HPA) should improve availability,
**not** model speed. Let's prove or disprove it with numbers.

---

## What's new in gateway v0.3

| Feature | Why |
|---|---|
| `POST /v1/generate/stream` (Server-Sent Events) | Tokens arrive as they are generated, like ChatGPT |
| `llm_time_to_first_token_seconds` (TTFT) | What users actually feel: how long until text appears |
| `MAX_INFLIGHT` (default 8 per pod) | Load shedding: when full, a fast 503 instead of piling up |
| `llm_inflight_requests` gauge | See how many generations are running right now |
| `llm_load_shed_total` counter | Count requests rejected because the pod was full |
| `--no-access-log` | Logs are JSON only (no duplicate uvicorn lines) |
| Helm: `hpa.yaml`, `maxInflight`, rate limit 600/min | Autoscaling and load-test-friendly limits |

Tested locally against a mock model server that, like Ollama, generates one request at a time:
streaming works, 6 parallel requests with `MAX_INFLIGHT=3` gave `503 503 503 200 200 200`,
the 3rd accepted request waited ~1.4 s for its first token (queueing), and Locust produced
p50/p95/p99 with a 5.6% error rate from load shedding.

---

## Step 0 – Start everything

1. Open **Ollama** (or `ollama serve` in its own window).
2. Start Docker: `open -a Docker`, then wait:
   ```bash
   until docker info >/dev/null 2>&1; do echo "waiting for Docker..."; sleep 5; done; echo ready
   ```

## Step 1 – Copy the Week 3 files into your project

Unzip `llmops-week3.zip` in Downloads, then:

```bash
cd ~/Downloads
cp llmops-week3/gateway/main.py llmops-week3/gateway/Dockerfile llmops-platform-week1/gateway/
rm -rf llmops-platform-week1/helm && cp -R llmops-week3/helm llmops-platform-week1/
cp -R llmops-week3/loadtest llmops-week3/WEEK3_GUIDE.md llmops-week3/BENCHMARKS.md llmops-week3/DECISIONS.md llmops-platform-week1/
cd llmops-platform-week1
grep -c "generate/stream" gateway/main.py     # should print a number above 0
```

## Step 2 – Try streaming locally first (2 minutes)

```bash
cd ~/Downloads/llmops-platform-week1/gateway
source .venv/bin/activate
lsof -ti :8000 | xargs kill -9 2>/dev/null
API_KEYS=dev-key uvicorn main:app --port 8000
```

In a second window (`-N` tells curl not to buffer, so you see tokens arrive live):

```bash
curl -N -X POST localhost:8000/v1/generate/stream -H "X-API-Key: dev-key" -H "Content-Type: application/json" -d '{"prompt":"Explain Kubernetes in two sentences.","max_tokens":60}'
```

You'll see `data: {"token": ...}` lines appear one by one, then a final line with `ttft_s` and
`latency_s`. Compare them: **TTFT is what the user waits; latency is the whole answer.**
Stop the server with Ctrl + C.

## Step 3 – Cluster, metrics-server, deploy v0.3

```bash
cd ~/Downloads/llmops-platform-week1
docker build -t llm-gateway:0.3 gateway/
kind get clusters                                   # if "llmops" is missing:
kind create cluster --config kind-config.yaml
kind load docker-image llm-gateway:0.3 --name llmops
```

Install **metrics-server** (gives `kubectl top` and powers the HPA). The patch is needed on kind:

```bash
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
kubectl -n kube-system patch deployment metrics-server --type=json -p '[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]'
kubectl -n kube-system rollout status deployment/metrics-server
```

Deploy (works whether the release exists or not):

```bash
helm upgrade --install llm-gateway helm/llm-gateway -n llmops --create-namespace \
  --set apiKeys="sk-$(openssl rand -hex 12)" --rollback-on-failure --timeout 3m
kubectl -n llmops get pods
export KEY=$(kubectl -n llmops get secret llm-gateway-secret -o jsonpath='{.data.API_KEYS}' | base64 -d)
curl localhost:8080/version                          # app_version 0.3.0, max_inflight 8
```

> If the release already exists from Week 2, `--set apiKeys=...` creates a new key. That's fine;
> just re-run the `export KEY=...` line.

## Step 4 – Install Locust

```bash
cd ~/Downloads/llmops-platform-week1
source gateway/.venv/bin/activate
pip install -r loadtest/requirements.txt
mkdir -p results
```

## Step 5 – Run the load tests

Keep a second window open with `kubectl -n llmops top pods` (re-run it during tests) to watch CPU.

**5a. Baseline: 1 user, 2 minutes**
```bash
API_KEY=$KEY locust -f loadtest/locustfile.py --host http://localhost:8080 --headless \
  -u 1 -r 1 -t 2m --csv results/baseline --csv-full-history --only-summary
python3 loadtest/report.py results/baseline
```

**5b. Step test: 1 → 2 → 4 → 8 users (4 minutes)**: finds where latency bends
```bash
SHAPE=steps API_KEY=$KEY locust -f loadtest/locustfile.py --host http://localhost:8080 --headless \
  --csv results/steps --csv-full-history --only-summary
python3 loadtest/report.py results/steps --history
```

**5c. Spike: 1 user → suddenly 10 → back to 1 (~3 minutes)**
```bash
SHAPE=spike API_KEY=$KEY locust -f loadtest/locustfile.py --host http://localhost:8080 --headless \
  --csv results/spike --csv-full-history --only-summary
python3 loadtest/report.py results/spike --history
```

**5d. Stress: 4 → 20 users (5 minutes)**: see how it fails
```bash
SHAPE=stress API_KEY=$KEY locust -f loadtest/locustfile.py --host http://localhost:8080 --headless \
  --csv results/stress --csv-full-history --only-summary
python3 loadtest/report.py results/stress --history
```

Paste each report into `BENCHMARKS.md`. **What to look for:**
- Does TTFT grow as users increase? (queueing at Ollama)
- At which user count does p95 jump?
- Do any errors appear (503 from load shedding, or timeouts)?

## Step 6 – Load shedding experiment

Lower the per-pod limit, then rerun the spike:

```bash
helm upgrade llm-gateway helm/llm-gateway -n llmops --reuse-values --set config.maxInflight=2 --rollback-on-failure --timeout 3m
SHAPE=spike API_KEY=$KEY locust -f loadtest/locustfile.py --host http://localhost:8080 --headless \
  --csv results/spike_shed --csv-full-history --only-summary
python3 loadtest/report.py results/spike_shed --history
curl -s localhost:8080/metrics | grep llm_load_shed_total
```

Compare with 5c: **more 503 errors, but lower latency for the requests that were accepted.**
That is the trade-off: reject fast, or make everyone wait. Set it back afterward:
`--set config.maxInflight=8`.

## Step 7 – Autoscaling (HPA) experiment

```bash
helm upgrade llm-gateway helm/llm-gateway -n llmops --reuse-values --set autoscaling.enabled=true --rollback-on-failure --timeout 3m
kubectl -n llmops get hpa
```

In one window: `kubectl -n llmops get hpa -w`. In another, rerun the stress test (5d).

Write down what happens. Two likely outcomes, and **both are good findings**:
1. **HPA does not scale**: gateway CPU stays low because pods spend their time *waiting*
   for Ollama. CPU is the wrong signal for an I/O-bound LLM gateway. (This is why Week 3's
   stretch goal, KEDA on in-flight requests, exists.)
2. **HPA scales up but latency doesn't improve**: more gateway pods, same single Ollama.
   The bottleneck is the model server, not the gateway.

Screenshot `kubectl get hpa` and `kubectl top pods` during the stress test.

## Step 8 (optional stretch) – Let Ollama run 2 at once

Stop Ollama, then start it with `OLLAMA_NUM_PARALLEL=2 ollama serve` and rerun the step test.
On 8 GB this may slow each request down. Compare throughput (req/s) and p95 with 5b.

## Step 9 – Commit and push

```bash
git add gateway loadtest helm WEEK3_GUIDE.md BENCHMARKS.md DECISIONS.md results
git commit -m "Week 3: SSE streaming, TTFT, load shedding, Locust load tests, HPA"
git pull --rebase && git push
```

## Cleanup

```bash
kind delete cluster --name llmops
```

---

## Evidence for the Week 3 article
- [ ] Streaming curl output (tokens arriving) + final ttft_s / latency_s
- [ ] Baseline vs steps table: TTFT and p95 as users grow
- [ ] Spike: what happened during the burst and after
- [ ] Stress: where errors start
- [ ] Load shedding: error rate vs latency trade-off (maxInflight 8 vs 2)
- [ ] HPA: `kubectl get hpa` + `kubectl top pods`, and whether latency changed
- [ ] One-sentence conclusion: where the bottleneck is and what you'd do in production

## Must-have vs stretch
- **Must-have:** Steps 2, 3, 5a, 5b, 5c, 7, 9
- **Stretch:** 5d stress, Step 6 load shedding, Step 8 OLLAMA_NUM_PARALLEL, KEDA
