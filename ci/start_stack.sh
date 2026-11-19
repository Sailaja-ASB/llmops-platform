#!/usr/bin/env bash
# Start Ollama, the RAG service and the gateway on one Docker network, configured exactly like the
# Helm charts (values read from helm/*/values.yaml). Used by the LLM answer gate (ci.yml) and the
# nightly full eval (nightly-eval.yml), so both test the same configuration.
#   env: OLLAMA_IMAGE, MODEL (required); images llm-gateway:ci and rag-service:ci must already be loaded
#   ports: gateway on localhost:8080, RAG service on localhost:8001
set -euo pipefail
: "${OLLAMA_IMAGE:?set OLLAMA_IMAGE}" "${MODEL:?set MODEL}"

val() { sed -n "s/^ *$1: *\"\([^\"]*\)\".*/\1/p" "$2"; }

docker network create ci
mkdir -p ~/ollama-models
docker run -d --name ollama --network ci -v ~/ollama-models:/root/.ollama/models "$OLLAMA_IMAGE"
for _ in $(seq 1 30); do docker exec ollama ollama list > /dev/null 2>&1 && break; sleep 2; done
docker exec ollama ollama pull "$MODEL"
docker run -d --name rag --network ci -p 8001:8001 \
  -e RETRIEVAL_MODE="$(val retrievalMode helm/rag-service/values.yaml)" \
  -e LOAD_RERANKER="$(val loadReranker helm/rag-service/values.yaml)" \
  rag-service:ci
docker run -d --name gw --network ci -p 8080:8000 \
  -e API_KEYS=ci-key -e OLLAMA_URL=http://ollama:11434 -e RAG_URL=http://rag:8001 -e MODEL_NAME="$MODEL" \
  -e RAG_PROMPT_VERSION="$(val ragPromptVersion helm/llm-gateway/values.yaml)" \
  -e GUARDRAILS_ENABLED="$(val guardrailsEnabled helm/llm-gateway/values.yaml)" \
  -e CITATION_REPAIR="$(val citationRepair helm/llm-gateway/values.yaml)" \
  llm-gateway:ci
for _ in $(seq 1 90); do
  curl -sf localhost:8001/readyz > /dev/null && curl -sf localhost:8080/readyz > /dev/null && break
  sleep 2
done
curl -sf localhost:8080/version
echo
# Warm up: load the model into memory so the first measured answer isn't a cold start.
curl -sf -X POST localhost:8080/v1/generate -H "X-API-Key: ci-key" -H "Content-Type: application/json" \
  -d '{"prompt":"Say OK.","max_tokens":5}' > /dev/null
echo "Stack ready: gateway :8080, rag-service :8001, model $MODEL"
