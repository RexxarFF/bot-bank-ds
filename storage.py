from __future__ import annotations

import json
import os
import shutil
import threading
import time
from copy import deepcopy
from pathlib import Path
from typing import Any


class AtomicJsonStore:
    """Small JSON-only state store with atomic replace and rotating backups."""

    def __init__(self, path: str | Path, default: dict[str, Any], backups: int = 10) -> None:
        self.path = Path(path)
        self.default = deepcopy(default)
        self.backups = max(1, backups)
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        (self.path.parent / "backups").mkdir(parents=True, exist_ok=True)
        (self.path.parent / "corrupt").mkdir(parents=True, exist_ok=True)

    def load(self) -> dict[str, Any]:
        with self._lock:
            if not self.path.exists():
                return deepcopy(self.default)
            try:
                with self.path.open("r", encoding="utf-8") as file:
                    value = json.load(file)
                if not isinstance(value, dict):
                    raise ValueError("root must be an object")
                result = deepcopy(self.default)
                result.update(value)
                return result
            except Exception:
                stamp = int(time.time() * 1000)
                target = self.path.parent / "corrupt" / f"{self.path.name}.{stamp}.corrupt"
                try:
                    shutil.move(str(self.path), str(target))
                except OSError:
                    pass
                return deepcopy(self.default)

    def save(self, value: dict[str, Any]) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists():
                stamp = int(time.time() * 1000)
                backup = self.path.parent / "backups" / f"{self.path.name}.{stamp}.bak"
                shutil.copy2(self.path, backup)
            temp = self.path.with_suffix(self.path.suffix + ".tmp")
            with temp.open("w", encoding="utf-8", newline="\n") as file:
                json.dump(value, file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp, self.path)
            self._prune()

    def update(self, mutator) -> dict[str, Any]:
        with self._lock:
            value = self.load()
            mutator(value)
            self.save(value)
            return value

    def _prune(self) -> None:
        folder = self.path.parent / "backups"
        files = sorted(folder.glob(f"{self.path.name}.*.bak"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[self.backups :]:
            try:
                old.unlink()
            except OSError:
                pass
