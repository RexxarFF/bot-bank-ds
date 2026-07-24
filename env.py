from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


class EnvError(RuntimeError):
    pass


def _ids(raw: str) -> set[int]:
    result: set[int] = set()
    for item in raw.replace(";", ",").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            value = int(item)
        except ValueError:
            continue
        if value > 0:
            result.add(value)
    return result


@dataclass(frozen=True)
class Env:
    token: str
    guild_id: int
    owner_ids: set[int]
    technical_bot_id: int
    bridge_timeout_seconds: float
    config_channel_id: int
    server_timezone: str

    @classmethod
    def load(cls, path: str | Path = ".env") -> "Env":
        load_dotenv(Path(path), override=False)
        token = os.getenv("DISCORD_TOKEN", "").strip()
        try:
            guild_id = int(os.getenv("GUILD_ID", "0") or 0)
        except ValueError:
            guild_id = 0
        try:
            technical_bot_id = int(os.getenv("TECHNICAL_BOT_ID", "0") or 0)
        except ValueError:
            technical_bot_id = 0
        try:
            config_channel_id = int(os.getenv("CONFIG_CHANNEL_ID", "0") or 0)
        except ValueError:
            config_channel_id = 0
        try:
            timeout = max(5.0, float(os.getenv("BRIDGE_TIMEOUT_SECONDS", "25") or 25))
        except ValueError:
            timeout = 25.0
        return cls(
            token=token,
            guild_id=guild_id,
            owner_ids=_ids(os.getenv("OWNER_IDS", "")),
            technical_bot_id=technical_bot_id,
            bridge_timeout_seconds=timeout,
            config_channel_id=config_channel_id,
            server_timezone=os.getenv("SERVER_TIMEZONE", "Europe/Moscow").strip() or "Europe/Moscow",
        )

    def validate(self) -> None:
        missing: list[str] = []
        if not self.token:
            missing.append("DISCORD_TOKEN")
        if not self.guild_id:
            missing.append("GUILD_ID")
        if not self.owner_ids:
            missing.append("OWNER_IDS")
        if not self.technical_bot_id:
            missing.append("TECHNICAL_BOT_ID")
        if missing:
            raise EnvError("Не заполнены переменные .env: " + ", ".join(missing))
