# Technical decisions

Short records of why the platform is built the way it is. Newest first.

## D30 – Pods report model readiness as a metric (Week 6)
**Decision:** gateway 0.5.3 exports `llm_model_server_ready` (set by every readiness check) and `llm_breaker_open`.
The model-outage alert reads these, not error rates.
**Why:** when Ollama is down, `/readyz` fails, Kubernetes removes every gateway pod from the Service and clients get
"connection refused". No request reaches a pod, so `llm_http_requests_total` shows no 5xx and an error-rate alert stays
silent during a full outage. Prometheus scrapes each pod directly, so a gauge the pod sets on its own readiness check is
still visible. Lesson: if the failure stops traffic, alert on a signal that does not need traffic.

## D29 – Alert rules as code, unit-tested in CI (Week 6)
**Decision:** seven Prometheus rules in `helm/observability/rules/llmops-alerts.yml`: gateway down, RAG down, model server
unreachable, breaker open, 5xx above 5%, p95 above 30 s, and fewer than 90% of answers citing their source over an hour.
`ci/alert_tests.yml` feeds made-up series to `promtool test rules` (healthy system stays quiet, each failure fires after its
`for` time, 401/429 never count as errors, too few answers never trigger the quality alert).
**Why:** an alert nobody has tested either never fires or fires all the time. Testing rules like code catches both before
merge (changing the 5% threshold to 20% fails the build). Thresholds come from measured numbers (p95 15.6 s normal, 31.2 s
under a burst) and the quality bar matches the CI answer gate (90%), so production and CI judge answers the same way.
Alerts show in Prometheus and on the dashboard's Alerts row; routing them to email or Slack (Alertmanager) is a later step.

## D28 – Liveness probes catch a hung process, not a slow one (Week 6)
**Decision:** every probe in the observability chart waits 5 s for an answer (Kubernetes' default is 1 s), and a
liveness probe restarts a pod only after 6 failures in a row (about 2 minutes). Grafana also requests 250m CPU.
**Why:** Grafana restarted four times, always while a dashboard was loading. The pod events said
`Liveness probe failed: context deadline exceeded`, and the exit code was 137, meaning Kubernetes killed it, not a crash.
On a laptop where the model uses most of the CPU, a busy but healthy Grafana answered its health check in more than
1 s, three times in a row. Restarting a slow pod makes things worse: the restart costs more CPU and every open dashboard
breaks. It is the same lesson as the Week 3 startup probe, from the other side: a probe has to fit the real machine.

## D27 – Count every HTTP response, including the ones rejected at the door (Week 6)
**Decision:** gateway 0.5.2 adds `llm_http_requests_total{route, code}`, incremented in the edge middleware for every
response. Routes outside a fixed list are counted as `other`, so the label can't grow without limit.
**Why:** 401, 413 and 429 are returned before any handler runs, so no metric saw them. In the first local test with
Prometheus, rate-limited requests were the largest share of traffic (0.45/s of 0.77/s) and were invisible: a dashboard
built on handler metrics would have looked calm while users were being turned away.

## D26 – Observability as its own chart, with dashboards as code (Week 6)
**Decision:** `helm/observability` installs one Prometheus and one Grafana in the cluster. Pods opt in to scraping with
`prometheus.io/*` annotations; Grafana starts with the datasource and the LLMOps dashboard provisioned from JSON in Git.
CI checks the Prometheus config and every dashboard query with `promtool`.
**Why:** dashboards made by hand in a UI get lost and drift; in Git they are reviewed and tested like code. Per-pod
targets fix the Week 2–3 problem of per-pod metrics: queries add pods up with `sum(...)`, and percentiles are computed
from summed histogram buckets, never by averaging per-pod p95s. Kept small (two pods, ~250 MB requested) for an 8 GB laptop;
a production cluster would use the Prometheus Operator, persistent storage and remote long-term storage.

## D25 – Publish images only after every gate, and never overwrite a release tag (Week 5)
**Decision:** on pushes to `main`, a last CI job pushes both images to GitHub Container Registry using the
workflow's own `GITHUB_TOKEN`: always as `sha-<commit>`, and as the version from the chart (e.g. `0.5.1`) only if that
tag does not exist yet. Each image carries an SBOM and a link back to the source commit.
**Why:** an image in the registry should mean "passed every gate". If a version tag could be overwritten, `0.5.1`
would point to different code on different days (the Week 4 bug where an image tagged 0.5.1 ran 0.5.0 code).
The commit tag gives traceability for every build; the version tag changes only when the version is bumped.

## D24 – Sample in the pull request, everything nightly (Week 5)
**Decision:** pull requests run 12 of the 47 questions through the model; a scheduled nightly job runs all 47
(same gates, one more refusal allowed) plus the held-out set, and keeps every answer as an artifact for 30 days.
**Why:** a fast PR gate keeps developers waiting minutes, not a quarter of an hour, while the nightly run catches what the
sample can miss. A failed nightly run emails the owner like any failed workflow.

## D23 – A held-out question set, written after tuning and never tuned on (Week 5)
**Decision:** 20 new questions (one or two per document, all four types) live in `questions_heldout.json`. They were
written on Nov 19 after the rag-v2 prompt was chosen, are reported but not gated until a baseline exists, and are not
edited after their results are seen. A unit test checks they never overlap the 47.
**Why:** rag-v2 went from 40/47 to 47/47 on the same 47 questions it was tuned on, which can be overfitting. Only
unseen questions show whether the gain is real. The set was kept harder on purpose: BM25 alone gets 16/20 here
(80%) against 43/47 (91.5%) on the benchmark, so it is not just the documents' own wording.

## D22 – An LLM answer gate on a sample, judged on what the model wrote (Week 5)
**Decision:** CI runs the real model (qwen2.5:3b on Ollama), the RAG service and the gateway together with the
Helm charts' settings, asks 12 of the 47 questions (every 4th: all 4 question types) at temperature 0, and fails if
the model itself cites the expected source in < 90% of answers, invents any citation, or refuses more than once.
It runs last, only after the cheaper jobs pass.
**Why:** prompt text is invisible to unit tests and lint; only the model's answers show a prompt regression.
The gate scores the model before citation repair (D18), so the guardrail cannot hide a worse prompt.
12 questions keep the job to minutes on a CPU runner; the full 47 run separately (nightly).
Temperature 0 reduces run-to-run noise in a pass/fail decision.

## D21 – Gate the configuration that ships, not just the code (Week 5)
**Decision:** CI starts the RAG image with `retrievalMode` and `loadReranker` read from the Helm chart's
values, and the retrieval gate checks Top-1 of that default mode (≥ 95% on the 47 questions).
**Why:** quality can drop without a code change. Switching `retrievalMode` to `dense` keeps every unit test
green but drops Top-1 from 100% to 91.5%; only a gate on the shipped config catches it.
The CI image uses CPU-only PyTorch (a build argument), avoiding ~2.5 GB of GPU libraries on x86 runners.

## D20 – Smoke-test the real image without a model (Week 5)
**Decision:** CI starts the actual gateway container with no Ollama and no RAG service and checks only
behaviour that must hold anyway: health, readiness 503, version, auth, input guardrail, validation, metrics.
**Why:** fast (seconds), no model download, and it tests the image that ships, not the source tree.
Model-dependent quality is checked by separate eval gates.

## D19 – One written-down lint rule set (Week 5)
**Decision:** ruff with E, W, F, I and B rules in `ruff.toml`; long guardrail regexes exempt from line length.
**Why:** CI must not depend on each developer's local settings. Adding B (bugbear) found 8 places where an
error was re-raised without its cause (`raise ... from exc`); fixed, so logs keep the original error.

## D18 – Measure model behaviour and guardrail fixes separately (Week 4 Day 4)
**Decision:** the eval reports what the model wrote (cited, invented, uncited, refused) apart from
the final output after guardrails (repaired citations, final citation accuracy).
**Why:** a guardrail that silently fixes answers can hide a model or prompt getting worse. Both numbers
are needed: one tells you about the model, the other about what users see.

## D17 – Output guardrails on streams work sentence by sentence (Week 4 Day 4)
**Decision:** with guardrails on, /v1/ask/stream holds tokens until a sentence (and any citation after
it) is complete, then sends the cleaned sentence: secrets redacted, invented [n] removed, citations repaired.
**Why:** once a token is sent it cannot be taken back, so token-by-token streaming would leak a secret
before any check runs. Cost: users see whole sentences instead of single tokens (first_text_s vs ttft_s).

## D16 – Citation repair only when the words match (Week 4 Day 4)
**Decision:** an uncited sentence gets [n] only if one retrieved chunk contains at least half of its
content words; otherwise it stays uncited and is counted (uncited_sentences).
**Why:** a confident wrong citation is worse than none. The repair never guesses: "RRF uses k = 60"
stays uncited because the chunk only says "Reciprocal Rank Fusion".

## D15 – Layered prompt-injection defence, pattern matching first (Week 4 Day 4)
**Decision:** regex patterns block known attack shapes in the question (HTTP 400) and drop retrieved
chunks that carry commands; text inside quotes is exempt; the prompt fences sources off as data.
**Why:** cheap (<1 ms), explainable and measurable. The quote exemption came from a real false positive:
the document that explains prompt injection was dropped for the question it answers. Known gaps
(other languages, misspellings, rephrasing) are measured by the red-team eval; the next layer would be a
small classifier model (e.g. Prompt Guard).

## D14 – Keep /v1/generate working when retrieval is down (Week 4)
**Decision:** gateway readiness depends on Ollama only. If the RAG service is unreachable, /v1/ask
returns a fast 503 ("retrieval service unavailable") and /v1/generate keeps working.
**Why:** one failing dependency should not take the whole gateway out of the Service.

## D13 – Numbered citations, checked by the gateway (Week 4)
**Decision:** sources are numbered [1]..[k] in the prompt; the gateway maps [n] in the answer back to
files and labels every answer cited / uncited / invalid_citation / no_answer (Prometheus counter).
**Why:** citations the system can verify. A number that was never retrieved is an invented source,
and Day 4 guardrails will act on it.

## D12 – Models baked into the image, startup probe for loading (Week 4)
**Decision:** the embedding model and reranker are downloaded at docker build; at runtime the pod is
offline (HF_HUB_OFFLINE=1). A startup probe allows up to 3 minutes for model loading.
**Why:** pods start without internet and every replica runs identical model files. The startup probe
applies the Week 3 lesson: never let liveness kill a pod that is slow but healthy.

## D11 – Hybrid retrieval by default, reranker optional (Week 4)
**Decision:** RETRIEVAL_MODE=hybrid (BM25 + dense, RRF k=60); hybrid_rerank available per request.
**Why:** on the 47-question benchmark dense and BM25 each reached 91.5% Top-1 and failed on different
questions; hybrid reached 100% for +0.1 ms. The cross-encoder added ~50 ms with no further gain.

## D10 – Retrieval as its own service (Week 4)
**Decision:** a separate rag-service (ClusterIP, port 8001) owns chunking, embeddings and ranking;
the gateway calls it over HTTP and passes X-Request-ID.
**Why:** retrieval and generation scale, deploy and get evaluated separately; the benchmark runs
against the deployed service directly. The 36-chunk corpus is held in memory; a large corpus
would use a vector database.

## D9 – Lenient liveness, cached readiness, long grace period (Week 3 fix)
**Decision:** liveness timeout 5 s / 6 failures; readiness timeout 3 s with a 10 s cached Ollama
check; 90 s termination grace, 60 s uvicorn graceful shutdown and a 5 s preStop sleep; HPA waits
3 minutes before scaling down.
**Why:** in the 20-user stress test, 1 s probe timeouts on a CPU-starved Mac made Kubernetes restart
pods that were slow but healthy, and an HPA scale-down killed a pod mid-stream. Both cut active
answers (HTTP 0 / 500). A probe should only restart a pod that is truly stuck, and shutdown must
outlast the longest stream.

## D8 – CPU-based HPA first, KEDA later (Week 3)
**Decision:** start with a CPU HPA (target 50%, 2–5 pods) and measure whether it reacts.
**Why:** it's the standard first step, but an LLM gateway mostly *waits* on the model, so CPU may
stay low under heavy load. If so, scale on in-flight requests or queue depth (KEDA) instead.

## D7 – Load shedding with MAX_INFLIGHT (Week 3)
**Decision:** each pod runs at most MAX_INFLIGHT generations; extra requests get a fast 503.
**Why:** an unbounded queue turns overload into minutes of waiting for everyone. A fast,
clear error lets clients retry or fall back, and keeps latency predictable for accepted requests.

## D6 – Stream tokens with Server-Sent Events (Week 3)
**Decision:** add POST /v1/generate/stream using SSE; keep /v1/generate for simple clients.
**Why:** time to first token is what users feel. SSE is plain HTTP, works through proxies and
is the same pattern most LLM APIs use. Errors after the stream starts are sent as an event,
because the 200 status is already on the wire.

## D5 – Retry only on connection errors (Week 2)
**Decision:** retry up to 2 times with exponential backoff only when the gateway cannot connect to Ollama.
**Why:** retrying a slow generation doubles load exactly when the model server is struggling.

## D4 – Circuit breaker in front of Ollama (Week 2)
**Decision:** after 3 connection failures, reject requests with a fast 503 for 15 s, then let one request test recovery.
**Why:** fail fast instead of making every user wait for timeouts during an outage.

## D3 – /healthz never depends on Ollama (Week 2)
**Decision:** liveness checks only the gateway process; readiness checks Ollama.
**Why:** a model-server outage should stop traffic to the pods, not restart healthy pods in a loop.

## D2 – Ollama runs on the host, outside Kubernetes (Week 2)
**Decision:** only the gateway runs in the kind cluster; pods reach Ollama via host.docker.internal.
**Why:** an 8 GB MacBook Air cannot comfortably run kind, the model and monitoring together. Scaling gateway pods does not scale Ollama, which stays the bottleneck (measured in Week 3).

## D1 – A gateway in front of the model (Week 1)
**Decision:** clients call a FastAPI gateway, never Ollama directly.
**Why:** one stable API, health probes, metrics and a single place for auth, limits and guardrails.
