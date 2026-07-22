from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from threading import RLock
from typing import Any


class StorageCorruptError(RuntimeError):
    """Raised when the local JSON storage file exists but cannot be parsed
    as a JSON object.

    Deliberately fails loudly instead of silently discarding the file or
    fabricating replacement data.
    """


class LocalJsonStore:
    """Minimal JSON-file-backed key/value store for local development
    persistence (Python standard library only).

    The whole store is a single JSON object on disk, keyed by collection
    name (e.g. "trips", "planning_states"). Every write reads the current
    on-disk state, replaces only the given collection, and atomically
    rewrites the whole file (temp file in the same directory + os.replace)
    so a crash mid-write cannot leave a corrupt/partial file. A missing file
    is treated as an empty store; a file that exists but is not a valid
    JSON object raises StorageCorruptError rather than being silently
    replaced with empty data.

    Not for production use: single file, in-process locking only (no
    cross-process/multi-worker coordination), no migrations, no schema
    versioning. See ARCHITECTURE.md for the production database plan.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = RLock()

    def read_collection(self, collection: str) -> dict[str, Any]:
        with self._lock:
            return self._read_all().get(collection, {})

    def write_collection(self, collection: str, records: dict[str, Any]) -> None:
        with self._lock:
            data = self._read_all()
            data[collection] = records
            self._write_all(data)

    def _read_all(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}

        raw = self._path.read_text(encoding="utf-8")
        if not raw.strip():
            return {}

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise StorageCorruptError(
                f"Local storage file at {self._path} is not valid JSON: {exc}. "
                "Fix or remove the file manually; it will not be overwritten "
                "automatically."
            ) from exc

        if not isinstance(data, dict):
            raise StorageCorruptError(
                f"Local storage file at {self._path} must contain a JSON "
                "object at the top level."
            )

        return data

    def _write_all(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)

        fd, tmp_name = tempfile.mkstemp(
            dir=str(self._path.parent),
            prefix=f".{self._path.name}.",
            suffix=".tmp",
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
                json.dump(data, tmp_file, indent=2, sort_keys=True)
                tmp_file.flush()
                os.fsync(tmp_file.fileno())
            os.replace(tmp_path, self._path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise


_stores: dict[str, LocalJsonStore] = {}
_stores_lock = RLock()


def get_local_json_store(path: Path) -> LocalJsonStore:
    """Return a process-wide store shared by every caller that resolves to
    the same file path.

    Repositories that persist different collections into the same JSON file
    (trips, planning states) go through this so they coordinate through one
    lock instead of racing each other's read-modify-write cycles.
    """
    key = str(path.resolve())
    with _stores_lock:
        store = _stores.get(key)
        if store is None:
            store = LocalJsonStore(path)
            _stores[key] = store
        return store
