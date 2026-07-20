from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from storage import AtomicJsonStore


DEFAULT_SETTINGS: dict[str, Any] = {
    "version": 6,
    "guild_id": 0,
    "config_channel_id": 0,
    "config_message_id": 0,
    "channels": {
        "access": 0,
        "business_application_panel": 0,
        "bank": 0,
        "business": 0,
        "government_fines": 0,
        "business_applications": 0,
        "logs": 0,
        "bridge": 0,
    },
    "roles": {
        "bank_access": [],
        "fine_issuers": [],
    },
    "users": {"admins": []},
    "branding": {
        "name": "FunFernus Bank",
        "color": "F2A93B",
        "banners": {"access": "", "bank": "", "business": "", "business_application": "", "government_fines": ""},
    },
    "texts": {
        "access": {
            "title": "Получить доступ к банку",
            "description": "Получите код в Minecraft командой /discordshop link и введите его через кнопку ниже.",
        },
        "business_application": {
            "title": "Открытие бизнеса",
            "description": "Подайте заявку на создание бизнеса через удобную форму.",
        },
        "bank": {
            "title": "FunFernus Bank",
            "description": "Личный кабинет, переводы, штрафы, казна и история операций.",
        },
        "business": {
            "title": "Управление бизнесом",
            "description": "Финансы, товары, категории, продвижение и оформление, открываемое за продажи.",
        },
        "government_fines": {
            "title": "Government • Штрафы",
            "description": "Выдача штрафов уполномоченными ролями и администраторами банка.",
        },
    },
    "features": {
        "transfers": True,
        "fines": True,
        "business_management": True,
        "treasury": True,
        "history": True,
    },
    "finance": {
        "transfer_fee_percent": 5.0,
        "transfer_minimum_fee": 1,
        "treasury_quick_amounts": [100, 500, 1000, 5000],
    },
    "recent_recipients": {},
    "panels": {},
    "posted": {},
}


def _deep_merge(default: Any, value: Any) -> Any:
    if isinstance(default, dict) and isinstance(value, dict):
        result = deepcopy(default)
        for key, item in value.items():
            result[key] = _deep_merge(default.get(key), item) if key in default else deepcopy(item)
        return result
    return deepcopy(value) if value is not None else deepcopy(default)


class RuntimeSettings:
    def __init__(self, path: str | Path = Path("data") / "settings.json") -> None:
        self.store = AtomicJsonStore(path, DEFAULT_SETTINGS, backups=20)
        self.data = _deep_merge(DEFAULT_SETTINGS, self.store.load())

    def save(self) -> None:
        self.store.save(self.data)

    def reload(self) -> None:
        self.data = _deep_merge(DEFAULT_SETTINGS, self.store.load())

    def get(self, path: str, default: Any = None) -> Any:
        value: Any = self.data
        for part in path.split("."):
            if not isinstance(value, dict) or part not in value:
                return default
            value = value[part]
        return value

    def set(self, path: str, value: Any) -> None:
        parts = path.split(".")
        node = self.data
        for part in parts[:-1]:
            child = node.get(part)
            if not isinstance(child, dict):
                child = {}
                node[part] = child
            node = child
        node[parts[-1]] = value
        self.save()

    def ids(self, path: str) -> set[int]:
        raw = self.get(path, []) or []
        if isinstance(raw, (str, int)):
            raw = [raw]
        result: set[int] = set()
        for item in raw:
            try:
                value = int(item)
            except (TypeError, ValueError):
                continue
            if value > 0:
                result.add(value)
        return result

    def channel(self, key: str) -> int:
        try:
            return int(self.get(f"channels.{key}", 0) or 0)
        except (TypeError, ValueError):
            return 0

    def feature(self, key: str) -> bool:
        return bool(self.get(f"features.{key}", False))
