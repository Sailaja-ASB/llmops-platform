FROM python:3.11-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py guardrails.py ./

# Non-root user (Kubernetes securityContext also enforces this)
RUN useradd -m -u 10001 appuser
USER 10001

EXPOSE 8000
# --no-access-log: our JSON logs already record every request
# --timeout-graceful-shutdown: let in-flight streams (up to ~50 s under load) finish when a pod stops
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "60", "--no-access-log"]
