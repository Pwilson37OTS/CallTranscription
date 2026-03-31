from abc import ABC, abstractmethod
from pathlib import Path
from typing import Union

from config import APP_DIR


class StorageBackend(ABC):
    @abstractmethod
    def save(self, file_bytes: bytes, filename: str) -> str:
        """Save file bytes and return the storage path."""

    @abstractmethod
    def load(self, path: str) -> bytes:
        """Load file bytes from the given path."""

    @abstractmethod
    def exists(self, path: str) -> bool:
        """Check if a file exists at the given path."""

    @abstractmethod
    def delete(self, path: str) -> None:
        """Delete a file at the given path."""

    @abstractmethod
    def resolve(self, path: str) -> Path:
        """Resolve a stored path to an absolute filesystem path."""


class LocalStorage(StorageBackend):
    def __init__(self, base_dir: Path = APP_DIR):
        self.base_dir = base_dir

    def save(self, file_bytes: bytes, filename: str) -> str:
        from config import CONVERTED_DIR
        dest = CONVERTED_DIR / filename
        with open(dest, "wb") as f:
            f.write(file_bytes)
        return str(dest.relative_to(self.base_dir))

    def load(self, path: str) -> bytes:
        abs_path = self.resolve(path)
        with open(abs_path, "rb") as f:
            return f.read()

    def exists(self, path: str) -> bool:
        return self.resolve(path).exists()

    def delete(self, path: str) -> None:
        abs_path = self.resolve(path)
        if abs_path.exists():
            abs_path.unlink()

    def resolve(self, path: str) -> Path:
        p = Path(path)
        if p.is_absolute():
            return p
        return self.base_dir / p


storage = LocalStorage()
