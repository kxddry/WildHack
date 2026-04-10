FROM python:3.11-slim

WORKDIR /app

ENV PYTHONPATH=/app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY services/prediction-service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY shared/team_pipeline ./team_pipeline/
COPY services/prediction-service/app/ ./app/

RUN mkdir -p ./models

EXPOSE 8000

# Container-level health probe. Uses stdlib urllib so no curl/wget install
# is required. The /health endpoint returns 200 with status="healthy" or
# status="mock"; both are accepted as live (compose-level dependents see
# "service_healthy" and start). status="degraded" produces a 200 too but
# with model_loaded=false / database_connected=false — we still need a
# stricter readiness signal at the app layer if that becomes load-bearing.
HEALTHCHECK --interval=10s --timeout=5s --start-period=40s --retries=6 \
    CMD python -c "import urllib.request,sys; r=urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=3); sys.exit(0 if r.status==200 else 1)" || exit 1

# --timeout-graceful-shutdown lets in-flight predictions finish before
# uvicorn closes the loop on SIGTERM. Compose's stop_grace_period must be
# >= this value to give us the full window before SIGKILL.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "25"]
