FROM python:3.11-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

# Non-root user (Kubernetes securityContext also enforces this)
RUN useradd -m -u 10001 appuser
USER 10001

EXPOSE 8000
# --timeout-graceful-shutdown lets in-flight requests finish when Kubernetes stops the pod
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "20"]
