# Benchmarks

All numbers measured on a MacBook Air M1 (2020, 8 GB), qwen2.5:3b (4-bit) on Ollama running on
the host (`OLLAMA_NUM_PARALLEL=1`), gateway in a kind cluster unless noted.

## Week 1 – single user, non-streaming (20 sequential requests, max_tokens 80)

| Metric | Gateway in Docker | Gateway on host |
|---|---|---|
| p50 latency | 2.54 s | 2.36 s |
| p95 latency | 6.65 s | 7.01 s |
| Mean throughput | 12.8 tokens/s | 13.0 tokens/s |

## Week 2 – single user through Kubernetes (2 pods)

| Metric | Value |
|---|---|
| p50 latency | 3.19 s |
| p95 latency | 7.24 s |
| Mean throughput | 11.8 tokens/s |
| Gateway overhead | ~50 ms average |

## Week 3 – concurrent users, streaming (Locust, max_tokens 48, 1–3 s think time)

Two gateway pods in kind, Ollama with `OLLAMA_NUM_PARALLEL=1` (one generation at a time).
TTFT = time to first token; "full" = time to the last token. Raw Locust CSVs are in [`results/`](results/).

| Test | Requests | Errors | Throughput | TTFT p50 | TTFT p95 | Full p95 |
|---|---|---|---|---|---|---|
| Baseline (1 user, 2 min) | 24 | 0% | 0.20 req/s | 0.10 s | 0.41 s | 4.2 s |
| Step (1 → 8 users) | 76 | 0% | 0.32 req/s | 4.0 s | 18 s | 22 s |
| Spike (1 → 10 → 1 users) | 38 | 0% | 0.24 req/s | 0.11 s | 26 s | 29 s |
| Stress (4 → 20 users), v0.3.0 | 145 | **30.3%** | 0.24 req/s | 16 s | 46 s | 48 s |
| Stress (4 → 20 users), v0.3.1 fix | 81 | **0%** | 0.27 req/s | 32 s | 54 s | 57 s |

**Step test, by number of users:** TTFT p50 0.1 s (1 user) → 2.1 s (2) → 6.4 s (4) → 19 s (8).
Requests queue at the single Ollama instance; throughput only rose from 0.20 to 0.32 req/s.

**Spike:** the median stayed at 0.11 s while p95 hit 26 s. The median hid what burst users felt.

**Stress, v0.3.0:** 44 failures (30 × HTTP 503, 11 × streams cut mid-way, 3 × HTTP 500). Cause: liveness
probes timed out on a CPU-starved node, Kubernetes restarted slow-but-healthy pods, and the HPA scaled
down while streams were running. Fix in v0.3.1 (see DECISIONS.md D9): lenient liveness, cached readiness,
preStop sleep, 90 s grace period, slower scale-down. Result: 0 failures in two repeat runs.

**HPA:** the CPU-based autoscaler scaled the gateway from 2 to 4 pods, yet TTFT p50 kept climbing with
users (10 s at 4 users → 52 s at 20). More gateway pods do not make one model server faster: autoscaling
has to target the bottleneck (model replicas, queue depth), not gateway CPU.

## Week 4 – RAG retrieval, citations and guardrails

47 benchmark questions over 18 documents with overlapping topics (36 chunks). Questions written before
any method was run: 12 direct, 14 paraphrase, 12 keyword, 9 confusable.

### Retrieval quality (Top-1 = expected source ranked first)

| Method | Top-1 | Hit@3 | MRR |
|---|---|---|---|
| Dense (all-MiniLM-L6-v2) | 43/47 (91.5%) | 47/47 | 0.957 |
| BM25 | 43/47 (91.5%) | 46/47 | 0.945 |
| **Hybrid (RRF, k = 60), default** | **47/47 (100%)** | 47/47 | 1.000 |
| Hybrid + cross-encoder reranker | 47/47 (100%) | 47/47 | 1.000 |

Dense missed exact identifiers, BM25 missed paraphrases; only one question was missed by both
(rank 2 in each), so fusion fixed every miss.

### Same quality, different speed (average retrieval time per query)

| Where it runs | Hybrid Top-1 | Hybrid | Hybrid + reranker |
|---|---|---|---|
| Mac, natively | 47/47 | 9 ms | 58 ms |
| Docker container | 47/47 | 33 ms | 566 ms |
| Kubernetes pod (1 CPU limit) | 47/47 | 81 ms | 2,102 ms (p95 4.3 s) |

The pod ran at 989m CPU against its 1-CPU limit (2 PyTorch threads): CPU throttling.
RAG image: 1.87 GB on disk (475 MB compressed). A ~390-token RAG prompt raised TTFT from 0.2 s to 7.2 s.

### Answers and citations, all 47 questions (`rag/eval/eval_ask.py`)

| Metric | Prompt rag-v1 | Prompt rag-v2 |
|---|---|---|
| Retrieval found the expected source (top 3) | 47/47 | 47/47 |
| Model cited the expected source | 40/47 | **47/47** |
| False "I don't know" | 4 | **0** |
| Answers with no citation | 3 | **0** |
| Invented citation numbers | 0 | 0 |
| Final, after the gateway's citation repair | 43/47 | 47/47 |
| End-to-end latency p50 / p95 | 2.6 s / 5.0 s | 2.7 s / 3.9 s |

rag-v2 was rerun after gateway 0.5.1 and scored 47/47 again. Caveat: rag-v2 was written after seeing
rag-v1 fail on these questions, so it still needs confirming on held-out questions (Week 5).

### Guardrails red team (`rag/eval/eval_guardrails.py`)

| Metric | Result |
|---|---|
| Attacks blocked by the input filter | 8/12 |
| Real questions wrongly blocked | 0/8 |
| Attacks that succeeded end to end | **0/12** (the 4 missed ones were refused by the grounded prompt) |

Missed by the filter (by design of the test): misspelled, Spanish, split words, polite rephrasing.
