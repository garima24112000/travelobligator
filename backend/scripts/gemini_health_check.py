"""Direct Gemini health check (manual, ONE live request).

Sends one trivial fixed prompt to the configured Gemini model through the
official `google-genai` SDK with the SDK's own retries switched off, and
prints only the model name and a result / error CATEGORY. Development
tooling only: nothing here is imported by `backend/app`.

    cd backend
    python scripts/gemini_health_check.py

Reads GEMINI_API_KEY and GEMINI_MODEL from the environment, else from the
repo-root `.env`. Never prints the key, a request URL, the model's answer
or raw exception text. Exit code: 0 = the model answered, 1 = the request
failed (the category says how), 2 = not configured.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_REPO_ENV_FILE = _BACKEND_DIR.parent / ".env"
_PROMPT = "Reply with the single word: ok"
_TIMEOUT_MS = 20_000


def _configuration() -> tuple[str | None, str | None]:
    """(key, model) from the environment, falling back to the repo-root dotenv."""
    values: dict[str, str | None] = {}
    if _REPO_ENV_FILE.exists():
        from dotenv import dotenv_values

        values = dotenv_values(_REPO_ENV_FILE)
    key = os.environ.get("GEMINI_API_KEY") or values.get("GEMINI_API_KEY")
    model = os.environ.get("GEMINI_MODEL") or values.get("GEMINI_MODEL")
    return (key or None), (model or None)


def main() -> int:
    sys.path.insert(0, str(_BACKEND_DIR))
    key, model = _configuration()
    if not key or not model:
        missing = [name for name, value in (("GEMINI_API_KEY", key), ("GEMINI_MODEL", model)) if not value]
        print("Not configured: " + ", ".join(missing) + " missing. Nothing was sent.")
        return 2

    from google import genai
    from google.genai import types

    from app.providers.ai_failure import health_failure_kind

    print(f"model: {model}")
    client = genai.Client(
        api_key=key,
        http_options=types.HttpOptions(timeout=_TIMEOUT_MS, retry_options=types.HttpRetryOptions(attempts=1)),
    )
    try:
        response = client.models.generate_content(
            model=model,
            contents=_PROMPT,
            config=types.GenerateContentConfig(
                temperature=0.0,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            ),
        )
    except Exception as exc:  # reported by CATEGORY only -- never the exception text
        status = getattr(exc, "code", None)
        print(f"result: error ({health_failure_kind(exc) or type(exc).__name__}" + (f", HTTP {status})" if isinstance(status, int) else ")"))
        return 1
    finally:
        client.close()

    answered = bool((getattr(response, "text", None) or "").strip())
    print("result: ok (the model answered)" if answered else "result: error (empty_answer)")
    return 0 if answered else 1


if __name__ == "__main__":
    raise SystemExit(main())
