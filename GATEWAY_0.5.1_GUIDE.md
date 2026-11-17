# Gateway 0.5.1: a refusal never carries a citation

**The bug (seen live on 0.5.0):** rag-v2 tells the model to end every sentence with a citation, so it also
cited its refusals: *"I don't know based on the provided documents. [1]"*. That points the user to a source
that does not answer the question.

**The fix:** `clean_refusals()` in `guardrails.py` turns a sentence that is exactly the refusal into the plain
refusal. A real answer that only mentions "I don't know" is never touched (unit test). Each fix is counted as
`llm_guardrail_blocks_total{stage="output",reason="refusal_citation"}`. Tests: 32 (was 29).

## Step 1 – Copy the files in
```bash
cd ~/Downloads && unzip -o llmops-0.5.1.zip
cp -R ~/Downloads/llmops-0.5.1/. ~/Downloads/llmops-platform-week1/
cd ~/Downloads/llmops-platform-week1
python3 -m pytest -q gateway/tests
```
Expect **32 passed**.

## Step 2 – Build and roll out
```bash
docker build -t llm-gateway:0.5.1 gateway/
kind load docker-image llm-gateway:0.5.1 --name llmops
helm upgrade llm-gateway helm/llm-gateway -n llmops --reset-then-reuse-values --rollback-on-failure --timeout 3m
export KEY=$(kubectl -n llmops get secret llm-gateway-secret -o jsonpath='{.data.API_KEYS}' | base64 -d)
curl -s localhost:8080/version; echo
```
Expect `app_version` 0.5.1.

## Step 3 – Check the fix, then rerun the evals (every change gets re-evaluated)
```bash
python3 rag/eval/eval_guardrails.py
python3 rag/eval/eval_ask.py --prompt-version rag-v2
```
Expect: the missed attacks now answer *"I don't know based on the provided documents."* with no [1], and
rag-v2 still 47/47 (no regression).

## Step 4 – Push
```bash
git add gateway helm results GATEWAY_0.5.1_GUIDE.md
git commit -m "Gateway 0.5.1: refusals never carry a citation; evals rerun, no regression"
git push
```
