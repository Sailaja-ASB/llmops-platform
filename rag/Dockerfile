FROM python:3.11-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 HF_HOME=/models

# CPU-only PyTorch. On Apple Silicon (arm64) the PyPI wheel is already CPU-only (~90 MB).
# On x86_64 the PyPI wheel pulls ~2.5 GB of GPU libraries, so CI builds with
#   --build-arg TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
ARG TORCH_INDEX_URL=https://pypi.org/simple
RUN pip install torch==2.5.1 --index-url ${TORCH_INDEX_URL}

COPY requirements.txt .
RUN pip install -r requirements.txt

# Bake both models into the image: pods start without internet access and every replica
# uses exactly the same model files (no surprise downloads in production).
RUN python -c "from sentence_transformers import SentenceTransformer, CrossEncoder; \
SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2'); \
CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')"

COPY fusion.py retriever.py main.py ./
COPY docs ./docs

RUN useradd -m -u 10001 appuser
USER 10001
# Never try to reach Hugging Face at runtime; HOME=/tmp gives libraries a writable place.
ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HOME=/tmp

EXPOSE 8001
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001", "--no-access-log"]
