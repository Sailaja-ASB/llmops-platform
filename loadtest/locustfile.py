"""Week 3 load test for the LLM gateway (Locust).

Each simulated user calls POST /v1/generate/stream and records:
  - "TTFT":          time until the first token
  - "full response": time until the last token
  - "stream open":   Locust's own entry (time to response headers); also counts failures
Locust measures streamed requests only up to the headers, so TTFT and full
response are timed here and reported as custom events.

Run (web UI):     locust -f loadtest/locustfile.py --host http://localhost:8080
Run (headless):   see WEEK3_GUIDE.md. Shapes are chosen with SHAPE=steps|spike|stress.

Environment:
  API_KEY     gateway key (X-API-Key header)
  MAX_TOKENS  output cap per request (default 48, keeps runs short on a laptop)
  SHAPE       optional load shape: steps, spike or stress (see below)
"""
import json
import os
import random
import time

from locust import HttpUser, LoadTestShape, between, events, task

API_KEY = os.getenv("API_KEY", "")
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "48"))
SHAPE = os.getenv("SHAPE", "").lower()

PROMPTS = [
    "What is Docker in one sentence?",
    "Explain Kubernetes in one sentence.",
    "What does an API gateway do? One sentence.",
    "Give one tip for writing clean Python code.",
    "What is retrieval-augmented generation? One sentence.",
]


class LLMUser(HttpUser):
    wait_time = between(1, 3)  # think time between requests, like a real user

    @task
    def stream_generate(self):
        body = {"prompt": random.choice(PROMPTS), "max_tokens": MAX_TOKENS}
        headers = {"Content-Type": "application/json"}
        if API_KEY:
            headers["X-API-Key"] = API_KEY

        start = time.perf_counter()
        ttft = None
        with self.client.post("/v1/generate/stream", json=body, headers=headers, stream=True,
                              name="stream open", catch_response=True, timeout=180) as resp:
            if resp.status_code != 200:
                resp.failure(f"HTTP {resp.status_code}")
                return
            ok = False
            for raw in resp.iter_lines():
                if not raw or not raw.startswith(b"data:"):
                    continue
                event = json.loads(raw[5:])
                if "token" in event and ttft is None:
                    ttft = time.perf_counter() - start
                if "error" in event:
                    resp.failure(event["error"])
                    return
                if event.get("done"):
                    ok = True
            if not ok:
                resp.failure("stream ended without done event")
                return
            resp.success()

        full = time.perf_counter() - start
        for name, seconds in (("TTFT", ttft), ("full response", full)):
            if seconds is not None:
                events.request.fire(request_type="SSE", name=name, response_time=seconds * 1000,
                                    response_length=0, exception=None, context={})


# ---------- Load shapes (only active when SHAPE is set) ----------
# Each stage: (end_time_seconds, users, spawn_rate)
STAGES = {
    # Capacity step test: find where latency bends
    "steps": [(60, 1, 1), (120, 2, 1), (180, 4, 2), (240, 8, 4)],
    # Spike: quiet, sudden burst, back to quiet
    "spike": [(60, 1, 1), (100, 10, 10), (160, 1, 10)],
    # Stress: keep adding users past the comfortable point to see how it fails
    "stress": [(60, 4, 2), (120, 8, 2), (180, 12, 2), (240, 16, 2), (300, 20, 2)],
}

if SHAPE in STAGES:
    class StagedShape(LoadTestShape):
        stages = STAGES[SHAPE]

        def tick(self):
            run_time = self.get_run_time()
            for end, users, rate in self.stages:
                if run_time < end:
                    return users, rate
            return None  # stop the test
