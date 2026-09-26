from __future__ import annotations

import pytest

from app import serve

# Section 200E: the production entry point runs exactly one worker, no reload, and reads PORT from the environment.


def test_one_worker_no_reload_and_a_safe_default_port(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("PORT", "HOST", "UVICORN_TIMEOUT_KEEP_ALIVE", "UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN", "UVICORN_ACCESS_LOG"):
        monkeypatch.delenv(name, raising=False)
    options = serve.uvicorn_options()
    assert options["workers"] == 1 and options["reload"] is False
    assert options["port"] == 8000 and options["host"] == "0.0.0.0"
    assert options["timeout_graceful_shutdown"] == 20 and options["timeout_keep_alive"] == 5


def test_port_and_timeouts_come_from_the_environment_and_bad_values_fall_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PORT", "8123")
    monkeypatch.setenv("UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN", "9")
    monkeypatch.setenv("UVICORN_ACCESS_LOG", "false")
    options = serve.uvicorn_options()
    assert options["port"] == 8123 and options["timeout_graceful_shutdown"] == 9 and options["access_log"] is False
    monkeypatch.setenv("PORT", "not-a-number")
    assert serve.uvicorn_options()["port"] == 8000


def test_main_starts_uvicorn_in_process_with_the_import_string(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple] = []
    monkeypatch.setattr(serve.uvicorn, "run", lambda app, **kw: calls.append((app, kw)))
    serve.main()
    assert calls[0][0] == "app.main:app" and calls[0][1]["workers"] == 1


def test_a_lingering_non_daemon_worker_thread_forces_a_prompt_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    exited: list[int] = []
    monkeypatch.setattr(serve.os, "_exit", lambda code: exited.append(code))
    release = threading.Event()
    worker = threading.Thread(target=release.wait, name="stuck-job", daemon=False)
    worker.start()
    try:
        serve._exit_if_worker_threads_linger()
        assert exited == [0]
    finally:
        release.set()
        worker.join()


def test_no_forced_exit_when_only_daemon_threads_remain(monkeypatch: pytest.MonkeyPatch) -> None:
    exited: list[int] = []
    monkeypatch.setattr(serve.os, "_exit", lambda code: exited.append(code))
    serve._exit_if_worker_threads_linger()
    assert exited == []
