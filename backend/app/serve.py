"""Production entry point (Section 200E): `python -m app.serve`.

Why a module and not a bare `uvicorn ...` command: the container must (a) run EXACTLY ONE web worker,
(b) read its port from the environment, and (c) receive SIGTERM directly -- with an exec-form
`CMD ["python", "-m", "app.serve"]` there is no shell in between (a shell would swallow signals and
cannot expand `${PORT}` in exec form). `uvicorn.run` installs its own SIGTERM/SIGINT handlers, so a
`docker stop` triggers uvicorn's graceful shutdown and then the FastAPI lifespan shutdown
(heartbeats stopped, Redis closed, engines disposed, one `app.shutdown` log line).

Exactly one worker per process on purpose: the metrics registry (`core/metrics.py`) and the job
heartbeat bookkeeping are process-local, so several workers in one container would fragment `/metrics`.
Scale by running more containers (each is stateless apart from that process-local bookkeeping).

Environment (all optional):
  HOST                              bind address (default 0.0.0.0 -- inside a container)
  PORT                              listen port (default 8000)
  UVICORN_TIMEOUT_KEEP_ALIVE        keep-alive seconds (default 5)
  UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN seconds uvicorn waits for in-flight requests on SIGTERM (default 20;
                                    keep it below the platform's stop grace period)
  UVICORN_ACCESS_LOG                "true"/"false" (default true; goes to stdout)
  FORWARDED_ALLOW_IPS               which peers uvicorn trusts for X-Forwarded-* (uvicorn's own variable;
                                    default `127.0.0.1`, i.e. NOT arbitrary clients). Set it to the trusted
                                    proxy/load-balancer address in a deployment (Section 203).
No `--reload`, no debug mode, no auto-migration.
"""

from __future__ import annotations

import os

import uvicorn


def _int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.environ.get(name, default)))
    except ValueError:
        return default


def uvicorn_options() -> dict:
    return {
        "host": os.environ.get("HOST", "0.0.0.0"),
        "port": _int("PORT", 8000),
        "workers": 1,
        "reload": False,
        "timeout_keep_alive": _int("UVICORN_TIMEOUT_KEEP_ALIVE", 5),
        "timeout_graceful_shutdown": _int("UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN", 20),
        "access_log": os.environ.get("UVICORN_ACCESS_LOG", "true").strip().lower() != "false",
        # uvicorn honours proxy headers only from FORWARDED_ALLOW_IPS (default: localhost)
        "proxy_headers": True,
    }


def _exit_if_worker_threads_linger() -> None:
    """After uvicorn's graceful shutdown (and the FastAPI lifespan shutdown) returned, a job's worker thread
    can still be running (a sync BackgroundTask cannot be cancelled) and would keep the interpreter -- and the
    container -- alive until the platform's SIGKILL. The lifespan already closed this process's jobs honestly as
    JOB_INTERRUPTED, so exit now instead of waiting to be killed."""
    import sys
    import threading

    lingering = [
        t.name for t in threading.enumerate() if t is not threading.main_thread() and not t.daemon
    ]
    if lingering:
        print(
            '{"level": "WARNING", "logger": "app.lifecycle", "event": "app.shutdown_forced", '
            '"message": "Exiting with %d worker thread(s) still running."}' % len(lingering),
            flush=True,
        )
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)


def main() -> None:
    uvicorn.run("app.main:app", **uvicorn_options())
    _exit_if_worker_threads_linger()


if __name__ == "__main__":
    main()
