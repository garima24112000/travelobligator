from __future__ import annotations

import importlib
import inspect
import pkgutil
import re
from pathlib import Path

from pydantic import BaseModel

import app.models as models_package
import app.schemas as schemas_package
from app.schemas.ai_candidate_review import AICandidateReviewResponseData
from app.schemas.itinerary_lineage import ItineraryBranchResponseData

# Final Batch 2: a lightweight contract check between `frontend/lib/types.ts` (hand-maintained, CLAUDE.md)
# and the backend. For every TypeScript type that has a backend model of the same name, every field the
# frontend declares must exist on that model -- a field the frontend reads but the backend never sends is
# drift. The reverse (a backend field the frontend does not declare) is normal: types.ts is a PARTIAL
# mirror. Only top-level keys are compared; nested shapes are checked through their own named types.

_TYPES = Path(__file__).resolve().parents[4] / "frontend" / "lib" / "types.ts"

# A TypeScript type whose backend counterpart is an API schema with a different name.
_EXPLICIT = {
    "ItineraryBranch": ItineraryBranchResponseData,
    "AICandidateReviewData": AICandidateReviewResponseData,
}


def _backend_models() -> dict[str, type[BaseModel]]:
    found: dict[str, type[BaseModel]] = {}
    for package in (models_package, schemas_package):
        for info in pkgutil.iter_modules(package.__path__):
            module = importlib.import_module(f"{package.__name__}.{info.name}")
            for name, cls in inspect.getmembers(module, inspect.isclass):
                if issubclass(cls, BaseModel) and cls.__module__ == module.__name__:
                    found.setdefault(name, cls)
    found.update(_EXPLICIT)
    return found


def _typescript_object_types() -> dict[str, set[str]]:
    text = _TYPES.read_text()
    result: dict[str, set[str]] = {}
    for match in re.finditer(r"export (?:interface|type) (\w+)\s*(?:extends [^{]+)?=?\s*\{", text):
        depth, index = 1, match.end()
        while depth and index < len(text):
            depth += {"{": 1, "}": -1}.get(text[index], 0)
            index += 1
        keys: set[str] = set()
        level = 0
        for line in text[match.end(): index - 1].splitlines():
            if level == 0 and (key := re.match(r"\s*(\w+)\??\s*:", line)):
                keys.add(key.group(1))
            level += line.count("{") - line.count("}")
        result[match.group(1)] = keys
    return result


def test_every_field_the_frontend_declares_exists_on_the_backend_model() -> None:
    backend = _backend_models()
    typescript = _typescript_object_types()
    compared = {name: keys for name, keys in typescript.items() if name in backend}
    assert len(compared) >= 40, "the parser no longer finds the shared types; fix the test, not the check"
    drift = {
        name: sorted(keys - set(backend[name].model_fields))
        for name, keys in compared.items()
        if keys - set(backend[name].model_fields)
    }
    assert drift == {}, f"types.ts declares fields the backend does not send: {drift}"


def test_the_batch2_fields_are_mirrored() -> None:
    typescript = _typescript_object_types()
    assert "promotion_refresh_available" in typescript["AICandidateReviewData"]
    assert {"change_requests_applicable", "change_requests_unavailable_reason"} <= typescript["RegenerationReadiness"]
