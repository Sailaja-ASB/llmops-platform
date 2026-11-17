# Technical decisions

Short records of why the platform is built the way it is. Newest first.

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
