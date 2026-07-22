from __future__ import annotations

from pathlib import Path

import pytest

from app.storage.local_json_store import LocalJsonStore, StorageCorruptError


def test_missing_file_starts_empty_without_crashing(tmp_path: Path) -> None:
    store = LocalJsonStore(tmp_path / "does_not_exist.json")
    assert store.read_collection("trips") == {}


def test_write_then_read_round_trips(tmp_path: Path) -> None:
    store = LocalJsonStore(tmp_path / "state.json")
    store.write_collection("trips", {"trip_1": {"trip_id": "trip_1", "status": "draft"}})

    assert store.read_collection("trips") == {
        "trip_1": {"trip_id": "trip_1", "status": "draft"}
    }


def test_fresh_store_instance_reloads_from_disk(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    first = LocalJsonStore(path)
    first.write_collection("trips", {"trip_1": {"trip_id": "trip_1", "status": "draft"}})

    second = LocalJsonStore(path)
    assert second.read_collection("trips") == {
        "trip_1": {"trip_id": "trip_1", "status": "draft"}
    }


def test_writing_one_collection_preserves_another(tmp_path: Path) -> None:
    store = LocalJsonStore(tmp_path / "state.json")
    store.write_collection("trips", {"trip_1": {"trip_id": "trip_1"}})
    store.write_collection("planning_states", {"trip_1": {"trip_id": "trip_1"}})

    assert store.read_collection("trips") == {"trip_1": {"trip_id": "trip_1"}}
    assert store.read_collection("planning_states") == {"trip_1": {"trip_id": "trip_1"}}


def test_write_is_atomic_and_leaves_no_temp_files(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    store = LocalJsonStore(path)
    store.write_collection("trips", {"trip_1": {"trip_id": "trip_1"}})

    files_in_dir = list(tmp_path.iterdir())
    assert files_in_dir == [path]


def test_corrupt_file_raises_clear_error_instead_of_fabricating_data(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("{not valid json", encoding="utf-8")

    store = LocalJsonStore(path)
    with pytest.raises(StorageCorruptError):
        store.read_collection("trips")

    # The corrupt file must be left untouched, not silently replaced.
    assert path.read_text(encoding="utf-8") == "{not valid json"


def test_non_object_json_top_level_raises_clear_error(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")

    store = LocalJsonStore(path)
    with pytest.raises(StorageCorruptError):
        store.read_collection("trips")
