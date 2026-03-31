from pathlib import Path

import pytest

from storage import LocalStorage


class TestLocalStorage:
    @pytest.fixture()
    def store(self, tmp_path, monkeypatch):
        monkeypatch.setattr("config.CONVERTED_DIR", tmp_path / "converted")
        (tmp_path / "converted").mkdir()
        return LocalStorage(base_dir=tmp_path)

    def test_save_and_load(self, store, tmp_path):
        data = b"fake audio content"
        rel_path = store.save(data, "test.wav")

        assert not Path(rel_path).is_absolute()
        loaded = store.load(rel_path)
        assert loaded == data

    def test_exists(self, store):
        store.save(b"data", "exists.wav")
        rel_path = store.save(b"data", "exists.wav")
        assert store.exists(rel_path) is True
        assert store.exists("data/converted/nope.wav") is False

    def test_delete(self, store):
        rel_path = store.save(b"data", "todelete.wav")
        assert store.exists(rel_path)
        store.delete(rel_path)
        assert not store.exists(rel_path)

    def test_delete_nonexistent_is_noop(self, store):
        store.delete("data/converted/ghost.wav")  # should not raise

    def test_resolve_relative(self, store, tmp_path):
        resolved = store.resolve("data/converted/test.wav")
        assert resolved == tmp_path / "data" / "converted" / "test.wav"

    def test_resolve_absolute(self, store, tmp_path):
        abs_path = tmp_path / "absolute" / "file.wav"
        resolved = store.resolve(str(abs_path))
        assert resolved == abs_path
