# Step 188C: aligned with this project's actual tested Python target
# (CI -- .github/workflows/ci.yml -- and CLAUDE.md both target 3.13;
# this image previously used 3.11, a version the test suite never
# actually ran under). This alone is a runtime-version alignment, not a
# "production-ready" claim -- no application behavior changed, and the
# full backend test suite was re-run unmodified to confirm nothing
# regressed under 3.13 before this change was made.
FROM python:3.13-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# Step 188C: a liveness-only healthcheck against the existing `/health`
# route (backend/app/main.py) -- no new route was added, and `/health`
# itself was not changed. This only confirms the FastAPI process is up
# and answering HTTP requests; it never checks Postgres, Redis, or any
# external provider, and must never be read as a readiness guarantee for
# any of those (see docs/14_backend_architecture.md for the full note).
# Uses the stdlib (urllib) already present in this image instead of
# curl/wget, neither of which is installed in python:3.13-slim -- an
# unhandled exception (connection refused, non-2xx via `raise_for_status`
# never being called, timeout) exits the `python -c` process non-zero,
# which is exactly what HEALTHCHECK needs to mark the container unhealthy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]