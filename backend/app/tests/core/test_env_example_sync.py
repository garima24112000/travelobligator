from __future__ import annotations

import re
from pathlib import Path

from app.core.config import Settings

# Final Batch 2: `.env.example` documents EVERY setting (CLAUDE.md: keep it in sync with config.py when
# adding one). A setting may appear commented out (`# NAME=`), but it must appear.

_ENV_EXAMPLE = (Path(__file__).resolve().parents[4] / ".env.example").read_text()


def test_every_setting_is_documented_in_env_example() -> None:
    documented = set(re.findall(r"^#? ?([A-Z][A-Z0-9_]*)=", _ENV_EXAMPLE, flags=re.MULTILINE))
    aliases = {field.alias for field in Settings.model_fields.values() if field.alias}
    assert sorted(aliases - documented) == []
