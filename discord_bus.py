from __future__ import annotations

import asyncio
import base64
import gzip
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

import discord

LOG = logging.getLogger("funfernus-bus")
PREFIX = "FFB3"
CHUNK_SIZE = 1450


class ApiError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class Assembly:
    total: int
    parts: list[str | None]
    updated_at: float

    @classmethod
    def create(cls, total: int, now: float) -> "Assembly":
        return cls(total=total, parts=[None] * total, updated_at=now)

    def complete(self) -> bool:
        return all(part is not None for part in self.parts)


class FunFernusApi:
    """Совместимый клиент, который общается с Minecraft через закрытый Discord-канал."""

    PATH_TO_OPERATION = {
        "/api/v1/health": "health",
        "/api/v1/link": "link",
        "/api/v1/unlink": "unlink",
        "/api/v1/profile": "profile",
        "/api/v1/transfer": "transfer",
        "/api/v1/treasury/donate": "treasury_donate",
        "/api/v1/history": "history",
        "/api/v1/fines": "fines",
        "/api/v1/fines/issue": "fine_issue",
        "/api/v1/fines/pay": "fine_pay",
        "/api/v1/fines/pay-all": "fines_pay_all",
        "/api/v1/admin/fines/cancel": "fine_admin_cancel",
        "/api/v1/admin/fines/waive": "fine_admin_waive",
        "/api/v1/business": "business",
        "/api/v1/business/deposit": "business_deposit",
        "/api/v1/business/withdraw": "business_withdraw",
        "/api/v1/business/catalog/promotion/buy": "catalog_promotion_buy",
        "/api/v1/business/upgrades/categories/buy": "business_upgrade_categories",
        "/api/v1/business/upgrades/tax/buy": "business_upgrade_tax",
        "/api/v1/business/applications/create": "business_application_create",
        "/api/v1/business/reopen": "business_reopen",
        "/api/v1/business/categories/create": "category_create",
        "/api/v1/business/categories/rename": "category_rename",
        "/api/v1/business/categories/delete": "category_delete",
        "/api/v1/business/products/edit": "product_edit",
        "/api/v1/admin/business-applications": "business_applications",
        "/api/v1/admin/business-applications/decision": "business_application_decide",
        "/api/v1/notifications": "notifications",
        "/api/v1/notifications/ack": "notifications_ack",
    }

    def __init__(self, bot: discord.Client, channel_id: int, technical_bot_id: int, timeout: float = 25.0) -> None:
        self.bot = bot
        self.channel_id = int(channel_id or 0)
        self.technical_bot_id = technical_bot_id
        self.timeout = max(5.0, timeout)
        self.pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self.assemblies: dict[str, Assembly] = {}
        self.cleanup_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self.cleanup_task is None:
            self.cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def close(self) -> None:
        if self.cleanup_task:
            self.cleanup_task.cancel()
            try:
                await self.cleanup_task
            except asyncio.CancelledError:
                pass
            self.cleanup_task = None
        for future in self.pending.values():
            if not future.done():
                future.set_exception(ApiError("BOT_SHUTDOWN", "Discord-бот выключается."))
        self.pending.clear()


    def set_channel_id(self, channel_id: int) -> None:
        self.channel_id = int(channel_id or 0)

    async def bind_channel(self, channel_id: int, guild_id: int) -> None:
        self.set_channel_id(channel_id)
        channel = self.bot.get_channel(self.channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(self.channel_id)
            except discord.HTTPException as exc:
                raise ApiError("BRIDGE_CHANNEL_NOT_FOUND", "Не удалось открыть выбранный технический канал.") from exc
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            raise ApiError("INVALID_BRIDGE_CHANNEL", "Технический канал должен быть текстовым.")
        try:
            await channel.send(
                f"{PREFIX}|BIND|{int(guild_id)}|{self.channel_id}",
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.Forbidden as exc:
            raise ApiError("BRIDGE_CHANNEL_FORBIDDEN", "Основной бот не может писать в выбранный технический канал.") from exc
        except discord.HTTPException as exc:
            raise ApiError("DISCORD_SEND_FAILED", "Не удалось отправить привязку техническому боту.") from exc

    async def health(self) -> dict[str, Any]:
        data = await self.request("health", {})
        return data or {}

    async def call(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
        retries: int = 1,
    ) -> Any:
        operation = self.PATH_TO_OPERATION.get(path)
        if operation is None:
            raise ApiError("UNKNOWN_OPERATION", f"Неизвестный внутренний маршрут: {path}")
        body: dict[str, Any] = {}
        if query:
            body.update({key: value for key, value in query.items() if value is not None})
        if payload:
            body.update({key: value for key, value in payload.items() if value is not None})
        return await self.request(operation, body, retries=retries)

    async def request(self, operation: str, payload: dict[str, Any], *, retries: int = 1) -> Any:
        request_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self.pending[request_id] = future
        envelope = json.dumps({"operation": operation, "payload": payload}, ensure_ascii=False, separators=(",", ":"))
        messages = self._encode("REQ", request_id, envelope)

        try:
            attempt = 0
            while True:
                await self._send_messages(messages)
                try:
                    response = await asyncio.wait_for(asyncio.shield(future), timeout=self.timeout)
                    break
                except asyncio.TimeoutError:
                    if attempt >= retries:
                        raise ApiError("MINECRAFT_TIMEOUT", "Minecraft-плагин не ответил. Проверьте технического бота и закрытый канал.")
                    attempt += 1
            if not response.get("ok", False):
                raise ApiError(str(response.get("code", "ERROR")), str(response.get("message", "Операция не выполнена.")))
            return response.get("data")
        finally:
            self.pending.pop(request_id, None)

    async def handle_message(self, message: discord.Message) -> None:
        if not self.channel_id or message.channel.id != self.channel_id:
            return
        if self.technical_bot_id and message.author.id != self.technical_bot_id:
            return
        if not message.author.bot:
            return
        part = self._parse(message.content)
        if part is None:
            return
        kind, request_id, index, total, payload = part
        if kind != "RES":
            return
        now = asyncio.get_running_loop().time()
        assembly = self.assemblies.get(request_id)
        if assembly is None or assembly.total != total:
            assembly = Assembly.create(total, now)
            self.assemblies[request_id] = assembly
        assembly.parts[index - 1] = payload
        assembly.updated_at = now
        if not assembly.complete():
            return
        self.assemblies.pop(request_id, None)
        try:
            packed = "".join(part or "" for part in assembly.parts)
            response = json.loads(self._decode(packed))
        except Exception as exc:
            LOG.warning("Повреждённый ответ Discord-моста: %s", exc)
            return
        future = self.pending.get(request_id)
        if future is not None and not future.done():
            future.set_result(response)

    async def _send_messages(self, messages: list[str]) -> None:
        # После изменения конфигурации или перезапуска всегда берём актуальный ID
        # технического канала из состояния бота. Это исправляет ложную ошибку
        # «технический канал не указан» при нажатии кнопки «Обновить».
        resolver = getattr(self.bot, "channel_id", None)
        if callable(resolver):
            try:
                configured = int(resolver("bridge") or 0)
                if configured:
                    self.channel_id = configured
            except (TypeError, ValueError):
                pass
        if not self.channel_id:
            raise ApiError("BRIDGE_NOT_CONFIGURED", "Технический канал ещё не выбран в конфигурации бота.")
        channel = self.bot.get_channel(self.channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(self.channel_id)
            except discord.HTTPException as exc:
                raise ApiError("BRIDGE_CHANNEL_NOT_FOUND", "Не удалось открыть технический Discord-канал.") from exc
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            raise ApiError("INVALID_BRIDGE_CHANNEL", "Технический канал должен быть текстовым каналом Discord.")
        for content in messages:
            try:
                await channel.send(content, allowed_mentions=discord.AllowedMentions.none())
            except discord.Forbidden as exc:
                raise ApiError("BRIDGE_CHANNEL_FORBIDDEN", "Основной бот не может писать в технический канал.") from exc
            except discord.HTTPException as exc:
                raise ApiError("DISCORD_SEND_FAILED", "Не удалось отправить запрос Minecraft-плагину.") from exc

    @staticmethod
    def _encode(kind: str, request_id: str, raw_json: str) -> list[str]:
        packed = gzip.compress(raw_json.encode("utf-8"))
        payload = base64.urlsafe_b64encode(packed).decode("ascii").rstrip("=")
        total = max(1, (len(payload) + CHUNK_SIZE - 1) // CHUNK_SIZE)
        return [
            f"{PREFIX}|{kind}|{request_id}|{index + 1}|{total}|{payload[index * CHUNK_SIZE:(index + 1) * CHUNK_SIZE]}"
            for index in range(total)
        ]

    @staticmethod
    def _decode(payload: str) -> str:
        payload += "=" * (-len(payload) % 4)
        return gzip.decompress(base64.urlsafe_b64decode(payload.encode("ascii"))).decode("utf-8")

    @staticmethod
    def _parse(content: str) -> tuple[str, str, int, int, str] | None:
        if not content.startswith(f"{PREFIX}|"):
            return None
        pieces = content.split("|", 5)
        if len(pieces) != 6:
            return None
        try:
            kind, request_id = pieces[1], pieces[2]
            index, total = int(pieces[3]), int(pieces[4])
            if not request_id or index < 1 or total < 1 or index > total or total > 128:
                return None
            return kind, request_id, index, total, pieces[5]
        except ValueError:
            return None

    async def _cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(60)
            cutoff = asyncio.get_running_loop().time() - 300
            stale = [request_id for request_id, assembly in self.assemblies.items() if assembly.updated_at < cutoff]
            for request_id in stale:
                self.assemblies.pop(request_id, None)
