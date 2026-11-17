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

Paste the output of `python3 loadtest/report.py results/<name> --history` under each heading.

### Baseline (1 user, 2 min)

### Step test (1 → 2 → 4 → 8 users)

### Spike (1 → 10 → 1 users)

### Stress (4 → 20 users)

### Load shedding (maxInflight 8 vs 2, spike)

### HPA (CPU 50%, min 2 / max 5)

### Conclusion
