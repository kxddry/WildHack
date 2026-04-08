FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Narrow COPY scope: only ship app/ — never the whole build context.
# A bare ``COPY . .`` would pull in __pycache__, .venv, .pytest_cache and
# anything else the host happens to have, even with .dockerignore present.
COPY app/ ./app/

EXPOSE 8002

HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=6 \
    CMD python -c "import urllib.request,sys; r=urllib.request.urlopen('http://127.0.0.1:8002/health',timeout=3); sys.exit(0 if r.status==200 else 1)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8002", "--timeout-graceful-shutdown", "25"]
