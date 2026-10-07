"""C# ``param.cs`` key=value files (id2label.txt, info.txt, legacy param.txt)."""

from __future__ import annotations

from pathlib import Path


class Param:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        self._data: dict[str, str] = {}
        if self.path and self.path.is_file():
            self.load(self.path)

    def load(self, path: str | Path) -> None:
        self.path = Path(path)
        self._data = {}
        text = self.path.read_text(encoding="utf-8", errors="replace")
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            self._data[key.strip()] = value.strip()

    def get_value(self, key: str, default: str = "") -> str:
        return self._data.get(key, default)

    def set_value(self, key: str, value: str) -> None:
        self._data[key] = value

    def items(self) -> dict[str, str]:
        return dict(self._data)
