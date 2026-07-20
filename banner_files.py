from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import discord


BANNER_DIRECTORY = Path("data") / "banners"
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
MAX_BANNER_SIZE = 10 * 1024 * 1024


def _banner_store(state: dict[str, Any]) -> dict[str, Any]:
    branding = state.setdefault("branding", {})
    banners = branding.setdefault("banners", {})
    if not isinstance(banners, dict):
        banners = {}
        branding["banners"] = banners
    return banners


def banner_filename(state: dict[str, Any], key: str) -> str:
    raw = _banner_store(state).get(key, "")
    if not isinstance(raw, str):
        return ""

    value = raw.strip()
    # Старые HTTP/HTTPS-значения намеренно не используются: настройка теперь
    # работает только через загруженные файлы.
    if not value or value.startswith(("http://", "https://")):
        return ""

    filename = Path(value).name
    if filename != value:
        return ""
    return filename


def banner_path(state: dict[str, Any], key: str) -> Path | None:
    filename = banner_filename(state, key)
    if not filename:
        return None
    path = BANNER_DIRECTORY / filename
    return path if path.is_file() else None


def apply_banner(embed: discord.Embed, state: dict[str, Any], key: str) -> bool:
    path = banner_path(state, key)
    if path is None:
        return False
    embed.set_image(url=f"attachment://{path.name}")
    return True


def make_banner_file(state: dict[str, Any], key: str) -> discord.File | None:
    path = banner_path(state, key)
    if path is None:
        return None
    return discord.File(path, filename=path.name)


def banner_display_name(state: dict[str, Any], key: str) -> str:
    path = banner_path(state, key)
    return path.name if path is not None else "не установлен"


def _remove_key_files(key: str) -> None:
    BANNER_DIRECTORY.mkdir(parents=True, exist_ok=True)
    for path in BANNER_DIRECTORY.glob(f"{key}.*"):
        if path.is_file():
            try:
                path.unlink()
            except OSError:
                pass


async def save_banner_attachment(
    state: dict[str, Any],
    key: str,
    attachment: discord.Attachment,
) -> str:
    suffix = Path(attachment.filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise ValueError(f"Поддерживаются только изображения: {allowed}.")

    if attachment.size > MAX_BANNER_SIZE:
        raise ValueError("Размер баннера не должен превышать 10 МБ.")

    content_type = (attachment.content_type or "").lower()
    if content_type and not content_type.startswith("image/"):
        raise ValueError("Прикреплённый файл не является изображением.")

    if suffix == ".jpeg":
        suffix = ".jpg"

    data = await attachment.read()
    if not data:
        raise ValueError("Discord вернул пустой файл.")
    if len(data) > MAX_BANNER_SIZE:
        raise ValueError("Размер баннера не должен превышать 10 МБ.")

    BANNER_DIRECTORY.mkdir(parents=True, exist_ok=True)
    _remove_key_files(key)

    filename = f"{key}{suffix}"
    target = BANNER_DIRECTORY / filename
    temporary = target.with_suffix(target.suffix + ".tmp")
    with temporary.open("wb") as file:
        file.write(data)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, target)

    _banner_store(state)[key] = filename
    return filename


def remove_banner(state: dict[str, Any], key: str) -> bool:
    existed = banner_path(state, key) is not None or bool(_banner_store(state).get(key))
    _remove_key_files(key)
    _banner_store(state)[key] = ""
    return existed
