"""Auth foundation package (Step 184B).

`passwords.py` (hashing/verification), `sessions.py` (signed session
cookies), and `dependencies.py` (the `get_current_user_id` FastAPI
dependency) live here. None of it is imported by `app/api/routes/trips.py`
yet -- no route is auth-gated, no owner check exists, `/trips/*` behavior
is completely unchanged. That wiring is Step 184D's job, after Step 184C
adds a real user repository and `/auth/*` routes.

Deliberately empty otherwise -- importing this package must never hash a
password, sign a token, or require `Settings.session_secret_key` to be
set, so no submodule is re-exported here.
"""
