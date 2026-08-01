from __future__ import annotations

import asyncio
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands, tasks

from banner_files import (
    apply_banner,
    banner_display_name,
    make_banner_file,
    remove_banner,
    save_banner_attachment,
)
from advanced_ui import (
    BusinessApplicationModal,
    FineAdminModal,
    FineTargetView,
    LinkCodeModal,
    open_business_dashboard,
    open_personal_cabinet,
)
from discord_bus import ApiError, FunFernusApi
from env import Env, EnvError
from runtime_settings import RuntimeSettings

BOT_PACKAGE_VERSION = "4.4.1-LINKFIX"
ENV = Env.load()
try:
    ENV.validate()
except EnvError as exc:
    print(str(exc), file=sys.stderr)
    raise SystemExit(1) from exc
TOKEN = ENV.token
GUILD_ID = ENV.guild_id
TECHNICAL_BOT_ID = ENV.technical_bot_id
OWNER_IDS = ENV.owner_ids
RUNTIME = RuntimeSettings(Path("data") / "settings.json")

if ENV.config_channel_id and not RUNTIME.get("config_channel_id", 0):
    RUNTIME.data["config_channel_id"] = ENV.config_channel_id
    RUNTIME.save()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler("bot.log", encoding="utf-8")],
)
LOG = logging.getLogger("funfernus-bank")

CHANNEL_LABELS = {
    "access": "Получение доступа",
    "business_application_panel": "Подача заявки на бизнес",
    "bank": "Банк",
    "business": "Управление бизнесом",
    "government_fines": "Government • штрафы",
    "business_applications": "Рассмотрение заявок на бизнес",
    "logs": "Логи банка",
    "bridge": "Техническая связь Minecraft",
}

ROLE_LABELS = {
    "bank_access": "Доступ к банку",
}

PANEL_LABELS = {
    "access": "Получение доступа",
    "business_application": "Заявка на бизнес",
    "bank": "Банк",
    "business": "Управление бизнесом",
    "government_fines": "Government • штрафы",
}

PANEL_TO_CHANNEL = {
    "access": "access",
    "business_application": "business_application_panel",
    "bank": "bank",
    "business": "business",
    "government_fines": "government_fines",
}


def brand_name() -> str:
    return str(RUNTIME.get("branding.name", "FunFernus Bank") or "FunFernus Bank").strip()


def brand_color() -> discord.Color:
    raw = str(RUNTIME.get("branding.color", "F2A93B") or "F2A93B").strip().removeprefix("#")
    try:
        return discord.Color(int(raw, 16))
    except ValueError:
        return discord.Color(0xF2A93B)


def brand_embed(
    title: str,
    description: str = "",
    *,
    error: bool = False,
    banner_key: str = "",
) -> discord.Embed:
    result = discord.Embed(
        title=title,
        description=description,
        color=discord.Color.red() if error else brand_color(),
        timestamp=datetime.now(timezone.utc),
    )
    result.set_footer(text=brand_name())
    if banner_key:
        apply_banner(result, RUNTIME.data, banner_key)
    return result


def panel_text(key: str, field: str, default: str) -> str:
    return str(RUNTIME.get(f"texts.{key}.{field}", default) or default).strip()


def truncate(value: Any, limit: int = 1000) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def money(value: Any) -> str:
    try:
        return f"{int(value):,}".replace(",", " ") + " АР"
    except (TypeError, ValueError):
        return "0 АР"


def discord_time(milliseconds: Any) -> str:
    try:
        return f"<t:{int(milliseconds) // 1000}:F>"
    except (TypeError, ValueError):
        return "—"


class FunFernusBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.guild_messages = True
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)
        self.state = RUNTIME.data
        self.state["guild_id"] = GUILD_ID
        self.api = FunFernusApi(
            self,
            int(self.state.get("channels", {}).get("bridge", 0) or 0),
            TECHNICAL_BOT_ID,
            timeout=ENV.bridge_timeout_seconds,
        )
        self.poll_lock = asyncio.Lock()
        self.business_application_lock = asyncio.Lock()
        self.banner_upload_users: set[int] = set()

    async def setup_hook(self) -> None:
        await self.api.start()
        # Регистрируем постоянные кнопки публичных банковских панелей.
        # Сами панели используют Components V2: большой баннер идёт первым,
        # затем текст и только после него кнопки.
        for panel_key in PANEL_LABELS:
            view, _ = public_panel_layout(self, panel_key, include_banner=False)
            self.add_view(view)
        self.add_view(ConfigPanelView(self))
        self._restore_decision_views()

        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()
        if not self.poller.is_running():
            self.poller.start()

    async def close(self) -> None:
        if self.poller.is_running():
            self.poller.cancel()
        await self.api.close()
        await super().close()

    def save_state(self) -> None:
        RUNTIME.data = self.state
        RUNTIME.save()

    def channel_id(self, key: str) -> int:
        try:
            return int(self.state.get("channels", {}).get(key, 0) or 0)
        except (TypeError, ValueError):
            return 0

    def role_ids(self, key: str) -> set[int]:
        values = self.state.get("roles", {}).get(key, []) or []
        result: set[int] = set()
        for item in values:
            try:
                value = int(item)
            except (TypeError, ValueError):
                continue
            if value > 0:
                result.add(value)
        return result

    def user_ids(self, key: str) -> set[int]:
        values = self.state.get("users", {}).get(key, []) or []
        result: set[int] = set()
        for item in values:
            try:
                value = int(item)
            except (TypeError, ValueError):
                continue
            if value > 0:
                result.add(value)
        return result

    def is_owner(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id in OWNER_IDS:
            return True
        return interaction.guild is not None and interaction.guild.owner_id == interaction.user.id

    def is_admin(self, interaction: discord.Interaction) -> bool:
        # Администратор банка — конкретный выбранный пользователь, а не роль.
        return self.is_owner(interaction) or interaction.user.id in self.user_ids("admins")

    def can_review(self, interaction: discord.Interaction) -> bool:
        return self.is_admin(interaction)

    def can_issue_fine(self, interaction: discord.Interaction) -> bool:
        # Штрафы выдаются только пользователями с внутренним уровнем ADMIN.
        # Discord-роли JUDGE/POLICE и их аналоги больше не участвуют в проверке.
        return self.is_admin(interaction)

    async def send_error(self, interaction: discord.Interaction, error: Exception) -> None:
        if isinstance(error, ApiError):
            title = "Ошибка операции"
            description = error.message
        else:
            LOG.error("Unexpected interaction error", exc_info=(type(error), error, error.__traceback__))
            title = "Внутренняя ошибка"
            description = "Произошла непредвиденная ошибка. Подробности записаны в bot.log."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=brand_embed(title, description, error=True), ephemeral=True)
            else:
                await interaction.response.send_message(embed=brand_embed(title, description, error=True), ephemeral=True)
        except discord.HTTPException:
            pass

    async def safe_log(self, title: str, description: str, *, error: bool = False) -> None:
        channel = self.get_channel(self.channel_id("logs"))
        if isinstance(channel, discord.TextChannel):
            try:
                await channel.send(embed=brand_embed(title, truncate(description, 4000), error=error))
            except discord.HTTPException:
                LOG.warning("Could not send Discord log message")

    def _restore_decision_views(self) -> None:
        for key, info in self.state.get("posted", {}).items():
            if not isinstance(info, dict) or info.get("status") != "PENDING":
                continue
            if info.get("type") != "business":
                continue
            message_id = int(info.get("message_id", 0) or 0)
            request_id = str(info.get("request_id", "") or "")
            if message_id and request_id:
                self.add_view(BusinessDecisionView(self, request_id), message_id=message_id)

    @tasks.loop(seconds=15)
    async def poller(self) -> None:
        # Пока технический канал не выбран, Minecraft опрашивать нельзя.
        # Это убирает бесполезные запросы и не мешает обработке slash-команд.
        bridge_channel_id = self.channel_id("bridge")
        if not bridge_channel_id:
            return
        self.api.set_channel_id(bridge_channel_id)
        async with self.poll_lock:
            try:
                await self._deliver_notifications()
                await self._post_business_applications()
            except ApiError as exc:
                LOG.warning("Polling API failed: %s (%s)", exc.message, exc.code)
            except Exception:
                LOG.exception("Polling loop failed")

    @poller.before_loop
    async def before_poller(self) -> None:
        await self.wait_until_ready()

    async def _deliver_notifications(self) -> None:
        data = await self.api.call("/api/v1/notifications", query={"limit": 50}, retries=0)
        notifications = (data or {}).get("notifications", [])
        acknowledged: list[str] = []
        for item in notifications:
            notification_id = str(item.get("id", "") or "")
            discord_id = str(item.get("discordId", "") or "")
            if not notification_id:
                continue
            if not discord_id.isdigit():
                acknowledged.append(notification_id)
                continue
            try:
                user = self.get_user(int(discord_id)) or await self.fetch_user(int(discord_id))
                await user.send(embed=brand_embed(str(item.get("title", brand_name())), str(item.get("message", ""))))
                acknowledged.append(notification_id)
            except (discord.Forbidden, discord.NotFound):
                acknowledged.append(notification_id)
                await self.safe_log("ЛС недоступны", f"Не удалось отправить уведомление пользователю <@{discord_id}>.", error=True)
            except discord.HTTPException as exc:
                LOG.warning("DM delivery failed for %s: %s", discord_id, exc)
        if acknowledged:
            await self.api.call("/api/v1/notifications/ack", method="POST", payload={"ids": acknowledged}, retries=0)

    async def _post_business_applications(self) -> None:
        # Один процесс публикации за раз. Раньше ручная публикация после формы
        # могла пересечься с poller и отправить одну заявку двумя сообщениями.
        async with self.business_application_lock:
            data = await self.api.call("/api/v1/admin/business-applications", retries=0)
            applications = (data or {}).get("applications", [])
            channel = self.get_channel(self.channel_id("business_applications"))
            if not isinstance(channel, discord.TextChannel):
                return
            posted = self.state.setdefault("posted", {})
            changed = False
            for item in applications:
                request_id = str(item.get("id", "") or "")
                state_key = f"business:{request_id}"
                if not request_id or state_key in posted:
                    continue

                # Восстанавливаем состояние после обновления/потери settings.json,
                # чтобы существующая заявка не публиковалась повторно.
                existing_message = None
                try:
                    async for old_message in channel.history(limit=100):
                        for old_embed in old_message.embeds:
                            footer = old_embed.footer.text if old_embed.footer else ""
                            if footer == f"ID: {request_id}":
                                existing_message = old_message
                                break
                        if existing_message is not None:
                            break
                except (discord.Forbidden, discord.HTTPException):
                    existing_message = None

                view = BusinessDecisionView(self, request_id)
                if existing_message is not None:
                    posted[state_key] = {
                        "type": "business",
                        "request_id": request_id,
                        "channel_id": channel.id,
                        "message_id": existing_message.id,
                        "status": "PENDING",
                    }
                    self.add_view(view, message_id=existing_message.id)
                    changed = True
                    continue

                message = await channel.send(embed=business_application_embed(item), view=view)
                posted[state_key] = {
                    "type": "business",
                    "request_id": request_id,
                    "channel_id": channel.id,
                    "message_id": message.id,
                    "status": "PENDING",
                }
                self.add_view(view, message_id=message.id)
                changed = True
            if changed:
                self.save_state()



bot = FunFernusBot()


class BusinessDecisionView(discord.ui.View):
    def __init__(self, client: FunFernusBot, request_id: str) -> None:
        super().__init__(timeout=None)
        self.client = client
        self.request_id = request_id
        self.approve.custom_id = f"ff:v36:business:approve:{request_id}"
        self.reject.custom_id = f"ff:v36:business:reject:{request_id}"

    @discord.ui.button(label="Одобрить", emoji="✅", style=discord.ButtonStyle.success, custom_id="ff:v36:business:approve")
    async def approve(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not self.client.can_review(interaction):
            await interaction.response.send_message("Заявки рассматривают только администраторы банка.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            result = await self._decide(interaction, "APPROVE", "")
            await self._finish(interaction, "APPROVED", result)
        except Exception as exc:
            await self.client.send_error(interaction, exc)

    @discord.ui.button(label="Отклонить", emoji="❌", style=discord.ButtonStyle.danger, custom_id="ff:v36:business:reject")
    async def reject(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not self.client.can_review(interaction):
            await interaction.response.send_message("Заявки рассматривают только администраторы банка.", ephemeral=True)
            return
        await interaction.response.send_modal(BusinessRejectModal(self, interaction.message))

    async def _decide(self, interaction: discord.Interaction, action: str, reason: str) -> dict[str, Any] | None:
        return await self.client.api.call(
            "/api/v1/admin/business-applications/decision",
            method="POST",
            payload={
                "application_id": self.request_id,
                "action": action,
                "reviewer_discord_id": str(interaction.user.id),
                "reviewer_name": interaction.user.display_name,
                "reason": reason,
            },
            retries=0,
        )

    async def _finish(
        self,
        interaction: discord.Interaction,
        status: str,
        result: dict[str, Any] | None,
        source_message: discord.Message | None = None,
    ) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        message = source_message or interaction.message
        old = message.embeds[0] if message and message.embeds else brand_embed("Заявка на бизнес")
        updated = discord.Embed.from_dict(old.to_dict())
        updated.color = discord.Color.green() if status == "APPROVED" else discord.Color.red()
        decision = "✅ Одобрено" if status == "APPROVED" else "❌ Отклонено"
        updated.add_field(name="Решение", value=f"{decision}\nРассмотрел: {interaction.user.mention}", inline=False)
        if result and result.get("reason"):
            updated.add_field(name="Причина", value=truncate(result.get("reason")), inline=False)
        if message:
            await message.edit(embed=updated, view=self)
        state_key = f"business:{self.request_id}"
        if state_key in self.client.state.get("posted", {}):
            self.client.state["posted"][state_key]["status"] = status
            self.client.save_state()
        await interaction.followup.send("Решение сохранено.", ephemeral=True)
        self.stop()


class BusinessRejectModal(discord.ui.Modal, title="Причина отказа"):
    reason = discord.ui.TextInput(label="Причина", style=discord.TextStyle.paragraph, min_length=3, max_length=800)

    def __init__(self, view: BusinessDecisionView, source_message: discord.Message | None) -> None:
        super().__init__(timeout=300)
        self.decision_view = view
        self.source_message = source_message

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            result = await self.decision_view._decide(interaction, "REJECT", self.reason.value.strip())
            await self.decision_view._finish(interaction, "REJECTED", result, self.source_message)
        except Exception as exc:
            await self.decision_view.client.send_error(interaction, exc)


def business_application_embed(item: dict[str, Any]) -> discord.Embed:
    result = brand_embed("Новая заявка на бизнес", f"**{item.get('businessName', '—')}**")
    discord_value = f"<@{item.get('discordId')}>" if item.get("discordId") else "не привязан"
    result.add_field(name="Владелец", value=f"Minecraft: **{item.get('ownerName', '—')}**\nDiscord: {discord_value}", inline=True)
    result.add_field(name="Тип", value=f"`{item.get('type', '—')}`", inline=True)
    result.add_field(name="Взнос", value=money(item.get("registrationFee")), inline=True)
    result.add_field(name="Описание", value=truncate(item.get("description") or "Не указано"), inline=False)
    result.add_field(name="Планируемое место", value=truncate(item.get("place") or "Не указано"), inline=False)
    result.add_field(name="Создана", value=discord_time(item.get("createdAt")), inline=True)
    result.set_footer(text=f"ID: {item.get('id', '—')}")
    return result


def mention_channel(channel_id: int) -> str:
    return f"<#{channel_id}>" if channel_id else "`не выбран`"


def mention_roles(role_ids: set[int]) -> str:
    return ", ".join(f"<@&{role_id}>" for role_id in sorted(role_ids)) if role_ids else "`не выбраны`"


def mention_users(user_ids: set[int]) -> str:
    return ", ".join(f"<@{user_id}>" for user_id in sorted(user_ids)) if user_ids else "`не выбраны`"


def config_embed(client: FunFernusBot) -> discord.Embed:
    result = brand_embed(
        "Конфигурация Discord-интерфейса FunFernus Bank",
        "Здесь настраиваются только каналы, роли, администраторы, тексты панелей, баннеры и их публикация. "
        "Экономика банка, цены, лимиты, ответы игровых команд и остальные настройки плагина изменяются только в YAML-файлах на хостинге.",
    )
    result.add_field(
        name="Каналы",
        value="\n".join(f"**{label}:** {mention_channel(client.channel_id(key))}" for key, label in CHANNEL_LABELS.items()),
        inline=False,
    )
    result.add_field(
        name="Роли",
        value="\n".join(f"**{label}:** {mention_roles(client.role_ids(key))}" for key, label in ROLE_LABELS.items()),
        inline=False,
    )
    result.add_field(name="Администраторы банка", value=mention_users(client.user_ids("admins")), inline=False)
    result.add_field(name="Оформление", value=f"Название: **{brand_name()}**\nЦвет: `#{str(RUNTIME.get('branding.color', 'F2A93B')).removeprefix('#')}`", inline=False)
    result.add_field(
        name="Файлы баннеров",
        value="\n".join(
            f"**{label}:** `{banner_display_name(client.state, key)}`"
            for key, label in PANEL_LABELS.items()
        ),
        inline=False,
    )
    result.set_footer(text="Discord-настройки и файловые баннеры сохраняются в папке data • экономика банка — только на хостинге")
    return result


async def ensure_config_panel(client: FunFernusBot, channel: discord.TextChannel | None = None) -> discord.Message | None:
    channel_id = int(client.state.get("config_channel_id", 0) or 0)
    if channel is None and channel_id:
        found = client.get_channel(channel_id)
        if isinstance(found, discord.TextChannel):
            channel = found
    if channel is None:
        return None
    message_id = int(client.state.get("config_message_id", 0) or 0)
    if message_id:
        try:
            message = await channel.fetch_message(message_id)
            await message.edit(embed=config_embed(client), view=ConfigPanelView(client))
            return message
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass
    message = await channel.send(embed=config_embed(client), view=ConfigPanelView(client))
    client.state["config_channel_id"] = channel.id
    client.state["config_message_id"] = message.id
    client.save_state()
    return message


async def refresh_config_panel(client: FunFernusBot) -> None:
    await ensure_config_panel(client)


def _panel_defaults(key: str) -> tuple[str, str]:
    defaults = {
        "access": ("Получить доступ к банку", "Получите код в Minecraft командой `/discordshop link` и введите его через кнопку ниже."),
        "business_application": ("Открытие бизнеса", "Подайте заявку на создание бизнеса через форму."),
        "bank": ("FunFernus Bank", "Личный кабинет, переводы, штрафы, казна и история операций."),
        "business": ("Управление бизнесом", "Финансы, товары, игровой каталог, сеть терминалов и цветовая тема."),
        "government_fines": ("Government • Штрафы", "Выдача штрафов уполномоченными ролями и администраторами банка."),
    }
    return defaults[key]


class PublicPanelButton(discord.ui.Button):
    def __init__(self, client: FunFernusBot, action: str) -> None:
        specs = {
            "access": dict(label="Получить доступ", emoji="🔐", style=discord.ButtonStyle.success, custom_id="ff:v36:access"),
            "business_application": dict(label="Подать заявку", emoji="📝", style=discord.ButtonStyle.primary, custom_id="ff:v36:business-application"),
            "bank": dict(label="Открыть банк", emoji="🏦", style=discord.ButtonStyle.primary, custom_id="ff:v36:bank"),
            "business": dict(label="Открыть управление бизнесом", emoji="📊", style=discord.ButtonStyle.primary, custom_id="ff:v36:business"),
            "fine_issue": dict(label="Выдать штраф", emoji="📄", style=discord.ButtonStyle.danger, custom_id="ff:v36:government-fine"),
            "fine_manage": dict(label="Отменить / погасить", emoji="🛡️", style=discord.ButtonStyle.secondary, custom_id="ff:v36:government-fine-admin"),
        }
        super().__init__(**specs[action])
        self.client = client
        self.action = action

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.action == "access":
            await interaction.response.send_modal(LinkCodeModal(self.client))
            return
        if self.action == "business_application":
            await interaction.response.send_modal(BusinessApplicationModal(self.client))
            return
        if self.action == "bank":
            await open_personal_cabinet(interaction, self.client)
            return
        if self.action == "business":
            await open_business_dashboard(interaction, self.client)
            return
        if self.action == "fine_issue":
            if not self.client.can_issue_fine(interaction):
                await interaction.response.send_message("У вашей роли нет права выдавать штрафы.", ephemeral=True)
                return
            await interaction.response.send_message(
                "Выберите игрока, которому нужно выдать штраф.",
                view=FineTargetView(self.client, interaction.user.id),
                ephemeral=True,
            )
            return
        if self.action == "fine_manage":
            if not self.client.is_admin(interaction):
                await interaction.response.send_message("Это действие доступно только администраторам банка.", ephemeral=True)
                return
            await interaction.response.send_modal(FineAdminModal(self.client))


def public_panel_layout(
    client: FunFernusBot,
    key: str,
    *,
    include_banner: bool = True,
) -> tuple[discord.ui.LayoutView, discord.File | None]:
    default_title, default_description = _panel_defaults(key)
    title = panel_text(key, "title", default_title)
    description = panel_text(key, "description", default_description)

    banner = make_banner_file(client.state, key) if include_banner else None
    children: list[discord.ui.Item[Any]] = []
    if banner is not None:
        children.append(
            discord.ui.MediaGallery(
                discord.MediaGalleryItem(
                    f"attachment://{banner.filename}",
                    description=f"Баннер панели: {PANEL_LABELS[key]}",
                )
            )
        )

    children.append(discord.ui.TextDisplay(f"# {title}\n{description}"))
    children.append(discord.ui.Separator())

    if key == "government_fines":
        row = discord.ui.ActionRow(
            PublicPanelButton(client, "fine_issue"),
            PublicPanelButton(client, "fine_manage"),
        )
    else:
        row = discord.ui.ActionRow(PublicPanelButton(client, key))
    children.append(row)
    children.append(discord.ui.TextDisplay(f"-# {brand_name()}"))

    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(*children, accent_color=brand_color()))
    return view, banner


async def publish_panel(client: FunFernusBot, key: str) -> discord.Message:
    channel_key = PANEL_TO_CHANNEL[key]
    channel_id = client.channel_id(channel_key)
    channel = client.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        raise RuntimeError(f"Сначала выберите канал «{CHANNEL_LABELS[channel_key]}».")

    panel_info = client.state.setdefault("panels", {}).get(key, {})
    if isinstance(panel_info, int):
        panel_info = {"message_id": panel_info, "channel_id": channel_id}
    message_id = int(panel_info.get("message_id", 0) or 0) if isinstance(panel_info, dict) else 0
    old_channel_id = int(panel_info.get("channel_id", 0) or 0) if isinstance(panel_info, dict) else 0

    if message_id and old_channel_id == channel.id:
        try:
            message = await channel.fetch_message(message_id)
            layout, banner = public_panel_layout(client, key)
            attachments = [banner] if banner is not None else []
            await message.edit(
                content=None,
                embed=None,
                attachments=attachments,
                view=layout,
            )
            return message
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    layout, banner = public_panel_layout(client, key)
    if banner is not None:
        message = await channel.send(view=layout, file=banner)
    else:
        message = await channel.send(view=layout)

    client.state.setdefault("panels", {})[key] = {"channel_id": channel.id, "message_id": message.id}
    client.save_state()
    return message


async def refresh_existing_panel(client: FunFernusBot, key: str) -> None:
    panel_info = client.state.setdefault("panels", {}).get(key, {})
    if isinstance(panel_info, int):
        panel_info = {"message_id": panel_info, "channel_id": client.channel_id(PANEL_TO_CHANNEL[key])}
    if not isinstance(panel_info, dict):
        return

    channel_id = int(panel_info.get("channel_id", 0) or 0)
    message_id = int(panel_info.get("message_id", 0) or 0)
    if not channel_id or not message_id:
        return

    channel = client.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        return

    try:
        message = await channel.fetch_message(message_id)
        layout, banner = public_panel_layout(client, key)
        attachments = [banner] if banner is not None else []
        await message.edit(
            content=None,
            embed=None,
            attachments=attachments,
            view=layout,
        )
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return


class ChannelPurposeSelect(discord.ui.Select):
    def __init__(self) -> None:
        super().__init__(
            placeholder="1. Выберите назначение",
            options=[discord.SelectOption(label=label, value=key) for key, label in CHANNEL_LABELS.items()],
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        assert isinstance(self.view, ChannelConfigView)
        self.view.selected_key = self.values[0]
        await interaction.response.edit_message(embed=self.view.render(), view=self.view)


class ChannelPicker(discord.ui.ChannelSelect):
    def __init__(self) -> None:
        super().__init__(
            placeholder="2. Выберите текстовый канал",
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            min_values=1,
            max_values=1,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        assert isinstance(self.view, ChannelConfigView)
        # Подтверждаем взаимодействие до сохранения и проверки Minecraft-моста.
        await interaction.response.defer()
        if not self.view.client.is_admin(interaction):
            await interaction.followup.send("У вас нет доступа к конфигурации.", ephemeral=True)
            return
        selected = self.values[0]
        key = self.view.selected_key
        channel_id = int(selected.id)
        self.view.client.state.setdefault("channels", {})[key] = channel_id
        self.view.client.save_state()
        note = ""
        if key == "bridge":
            self.view.client.api.set_channel_id(channel_id)
            try:
                await self.view.client.api.bind_channel(channel_id, GUILD_ID)
                note = " Технический канал привязан."
            except Exception as exc:
                note = f" Канал сохранён, но мост пока не подтвердил привязку: {exc}"
        await refresh_config_panel(self.view.client)
        await interaction.edit_original_response(
            embed=self.view.render(f"Сохранено: **{CHANNEL_LABELS[key]}** → <#{channel_id}>.{note}"),
            view=self.view,
        )


class ChannelConfigView(discord.ui.View):
    def __init__(self, client: FunFernusBot) -> None:
        super().__init__(timeout=600)
        self.client = client
        self.selected_key = "access"
        self.add_item(ChannelPurposeSelect())
        self.add_item(ChannelPicker())

    def render(self, status: str = "") -> discord.Embed:
        text = f"Назначение: **{CHANNEL_LABELS[self.selected_key]}**\nКатегории здесь не выбираются — права каналов задаются вручную в Discord."
        if status:
            text += f"\n\n{status}"
        return brand_embed("Настройка каналов", text)


class RolePurposeSelect(discord.ui.Select):
    def __init__(self) -> None:
        super().__init__(
            placeholder="1. Выберите назначение роли",
            options=[discord.SelectOption(label=label, value=key) for key, label in ROLE_LABELS.items()],
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        assert isinstance(self.view, AccessConfigView)
        self.view.selected_key = self.values[0]
        await interaction.response.edit_message(embed=self.view.render(), view=self.view)


class RolePicker(discord.ui.RoleSelect):
    def __init__(self) -> None:
        super().__init__(placeholder="2. Выберите роли", min_values=1, max_values=10, row=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        assert isinstance(self.view, AccessConfigView)
        if not self.view.client.is_admin(interaction):
            await interaction.response.send_message("У вас нет доступа.", ephemeral=True)
            return
        values = sorted({int(role.id) for role in self.values})
        self.view.client.state.setdefault("roles", {})[self.view.selected_key] = values
        self.view.client.save_state()
        await refresh_config_panel(self.view.client)
        await interaction.response.edit_message(embed=self.view.render("Роли сохранены."), view=self.view)


class AdminUserSelect(discord.ui.UserSelect):
    def __init__(self) -> None:
        super().__init__(placeholder="Выберите администраторов банка", min_values=1, max_values=10, row=2)

    async def callback(self, interaction: discord.Interaction) -> None:
        assert isinstance(self.view, AccessConfigView)
        if not self.view.client.is_admin(interaction):
            await interaction.response.send_message("У вас нет доступа.", ephemeral=True)
            return
        values = sorted({int(user.id) for user in self.values if not user.bot})
        self.view.client.state.setdefault("users", {})["admins"] = values
        self.view.client.save_state()
        await refresh_config_panel(self.view.client)
        await interaction.response.edit_message(embed=self.view.render("Администраторы сохранены."), view=self.view)


class AccessConfigView(discord.ui.View):
    def __init__(self, client: FunFernusBot) -> None:
        super().__init__(timeout=600)
        self.client = client
        self.selected_key = "bank_access"
        self.add_item(RolePurposeSelect())
        self.add_item(RolePicker())
        self.add_item(AdminUserSelect())

    def render(self, status: str = "") -> discord.Embed:
        text = (
            f"Настраиваемые роли: **{ROLE_LABELS[self.selected_key]}**\n"
            f"Текущие роли: {mention_roles(self.client.role_ids(self.selected_key))}\n\n"
            f"Администраторы банка: {mention_users(self.client.user_ids('admins'))}\n"
            "Рассматривать заявки могут эти же администраторы. Отдельной роли рассмотрения нет."
        )
        if status:
            text += f"\n\n{status}"
        return brand_embed("Роли и администраторы", text)

    @discord.ui.button(label="Очистить выбранные роли", emoji="🧹", style=discord.ButtonStyle.secondary, row=3)
    async def clear_roles(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not self.client.is_admin(interaction):
            await interaction.response.send_message("У вас нет доступа.", ephemeral=True)
            return
        self.client.state.setdefault("roles", {})[self.selected_key] = []
        self.client.save_state()
        await refresh_config_panel(self.client)
        await interaction.response.edit_message(embed=self.render("Роли очищены."), view=self)

    @discord.ui.button(label="Очистить администраторов", emoji="🧹", style=discord.ButtonStyle.danger, row=3)
    async def clear_admins(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not self.client.is_owner(interaction):
            await interaction.response.send_message("Очищать список администраторов может только владелец.", ephemeral=True)
            return
        self.client.state.setdefault("users", {})["admins"] = []
        self.client.save_state()
        await refresh_config_panel(self.client)
        await interaction.response.edit_message(embed=self.render("Список администраторов очищен."), view=self)


class BrandingModal(discord.ui.Modal, title="Оформление бота"):
    name = discord.ui.TextInput(label="Название", min_length=2, max_length=80)
    color = discord.ui.TextInput(label="HEX-цвет без #", min_length=6, max_length=7)

    def __init__(self, client: FunFernusBot) -> None:
        super().__init__(timeout=300)
        self.client = client
        self.name.default = brand_name()
        self.color.default = str(RUNTIME.get("branding.color", "F2A93B"))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = self.color.value.strip().removeprefix("#")
        if not re.fullmatch(r"[0-9A-Fa-f]{6}", raw):
            await interaction.response.send_message("Цвет должен содержать ровно 6 HEX-символов.", ephemeral=True)
            return
        self.client.state.setdefault("branding", {})["name"] = self.name.value.strip()
        self.client.state["branding"]["color"] = raw.upper()
        self.client.save_state()
        await refresh_config_panel(self.client)
        await interaction.response.send_message("Оформление сохранено.", ephemeral=True)


class PanelChoiceSelect(discord.ui.Select):
    def __init__(self, action: str) -> None:
        self.action = action
        super().__init__(
            placeholder="Выберите панель",
            options=[discord.SelectOption(label=label, value=key) for key, label in PANEL_LABELS.items()],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        assert isinstance(self.view, PanelChoiceView)
        key = self.values[0]
        if self.action == "text":
            await interaction.response.send_modal(PanelTextModal(self.view.client, key))
        elif self.action == "banner":
            view = BannerFileView(self.view.client, key)
            await interaction.response.edit_message(
                content=(
                    f"**{PANEL_LABELS[key]}**\n"
                    f"Текущий файл: `{banner_display_name(self.view.client.state, key)}`\n\n"
                    "Баннеры загружаются только файлами. Ссылки больше не используются."
                ),
                view=view,
            )
        elif self.action == "preview":
            layout, banner = public_panel_layout(self.view.client, key)
            if banner is not None:
                await interaction.response.send_message(view=layout, file=banner, ephemeral=True)
            else:
                await interaction.response.send_message(view=layout, ephemeral=True)
        else:
            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                message = await publish_panel(self.view.client, key)
                await interaction.followup.send(f"Панель **{PANEL_LABELS[key]}** опубликована: {message.jump_url}", ephemeral=True)
            except Exception as exc:
                await self.view.client.send_error(interaction, exc)


class PanelChoiceView(discord.ui.View):
    def __init__(self, client: FunFernusBot, action: str) -> None:
        super().__init__(timeout=300)
        self.client = client
        self.add_item(PanelChoiceSelect(action))


class PanelTextModal(discord.ui.Modal):
    title_input = discord.ui.TextInput(label="Заголовок", min_length=2, max_length=100)
    description_input = discord.ui.TextInput(label="Описание", style=discord.TextStyle.paragraph, min_length=3, max_length=1500)

    def __init__(self, client: FunFernusBot, key: str) -> None:
        super().__init__(title=f"Текст: {PANEL_LABELS[key]}", timeout=300)
        self.client = client
        self.key = key
        default_title, default_description = _panel_defaults(key)
        self.title_input.default = panel_text(key, "title", default_title)
        self.description_input.default = panel_text(key, "description", default_description)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        texts = self.client.state.setdefault("texts", {}).setdefault(self.key, {})
        texts["title"] = self.title_input.value.strip()
        texts["description"] = self.description_input.value.strip()
        self.client.save_state()
        await interaction.response.send_message("Текст панели сохранён.", ephemeral=True)


class BankBannerUploadModal(discord.ui.Modal):
    def __init__(self, client: FunFernusBot, key: str) -> None:
        super().__init__(title=f"Баннер: {PANEL_LABELS[key]}", timeout=300)
        self.client = client
        self.key = key
        self.file_field = discord.ui.Label(
            text="Выберите файл баннера",
            description="PNG, JPG, JPEG, WEBP или GIF. Максимум 10 МБ.",
            component=discord.ui.FileUpload(
                custom_id=f"ffbank_banner_{key}",
                required=True,
                min_values=1,
                max_values=1,
            ),
        )
        self.add_item(self.file_field)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not self.client.is_admin(interaction):
            await interaction.response.send_message("У вас нет доступа.", ephemeral=True)
            return

        component = self.file_field.component
        attachment = component.values[0] if isinstance(component, discord.ui.FileUpload) and component.values else None
        if attachment is None:
            await interaction.response.send_message("Файл не выбран.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            filename = await save_banner_attachment(self.client.state, self.key, attachment)
            self.client.save_state()
            await refresh_existing_panel(self.client, self.key)
            await refresh_config_panel(self.client)
        except (ValueError, discord.HTTPException, OSError) as exc:
            await interaction.followup.send(f"Не удалось сохранить баннер: {exc}", ephemeral=True)
            return

        await interaction.followup.send(
            f"Баннер **{PANEL_LABELS[self.key]}** сохранён из файла `{filename}`. "
            "Опубликованная панель обновлена автоматически.",
            ephemeral=True,
        )


class BannerFileView(discord.ui.View):
    def __init__(self, client: FunFernusBot, key: str) -> None:
        super().__init__(timeout=300)
        self.client = client
        self.key = key

    @discord.ui.button(label="Загрузить файл", emoji="📎", style=discord.ButtonStyle.primary)
    async def upload(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not self.client.is_admin(interaction):
            await interaction.response.send_message("У вас нет доступа.", ephemeral=True)
            return
        await interaction.response.send_modal(BankBannerUploadModal(self.client, self.key))

    @discord.ui.button(label="Удалить баннер", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def delete(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not self.client.is_admin(interaction):
            await interaction.response.send_message("У вас нет доступа.", ephemeral=True)
            return
        remove_banner(self.client.state, self.key)
        self.client.save_state()
        await refresh_existing_panel(self.client, self.key)
        await refresh_config_panel(self.client)
        await interaction.response.edit_message(
            content=f"Баннер **{PANEL_LABELS[self.key]}** удалён.",
            view=None,
        )


class ConfigPanelView(discord.ui.View):
    def __init__(self, client: FunFernusBot) -> None:
        super().__init__(timeout=None)
        self.client = client

    async def allowed(self, interaction: discord.Interaction) -> bool:
        if self.client.is_admin(interaction):
            return True
        await interaction.response.send_message("У вас нет доступа к конфигурации бота.", ephemeral=True)
        return False

    @discord.ui.button(label="Каналы", emoji="🗂️", style=discord.ButtonStyle.primary, custom_id="ffcfg:v381:channels", row=0)
    async def channels(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await self.allowed(interaction):
            return
        view = ChannelConfigView(self.client)
        await interaction.response.send_message(embed=view.render(), view=view, ephemeral=True)

    @discord.ui.button(label="Роли и админы", emoji="🛡️", style=discord.ButtonStyle.primary, custom_id="ffcfg:v381:access", row=0)
    async def access(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await self.allowed(interaction):
            return
        view = AccessConfigView(self.client)
        await interaction.response.send_message(embed=view.render(), view=view, ephemeral=True)

    @discord.ui.button(label="Оформление", emoji="🎨", style=discord.ButtonStyle.primary, custom_id="ffcfg:v381:branding", row=0)
    async def branding(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await self.allowed(interaction):
            return
        await interaction.response.send_modal(BrandingModal(self.client))

    @discord.ui.button(label="Тексты", emoji="✏️", style=discord.ButtonStyle.secondary, custom_id="ffcfg:v381:texts", row=1)
    async def texts(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await self.allowed(interaction):
            return
        await interaction.response.send_message("Выберите панель.", view=PanelChoiceView(self.client, "text"), ephemeral=True)

    @discord.ui.button(label="Баннеры", emoji="🖼️", style=discord.ButtonStyle.secondary, custom_id="ffcfg:v381:banners", row=1)
    async def banners(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await self.allowed(interaction):
            return
        await interaction.response.send_message("Выберите панель.", view=PanelChoiceView(self.client, "banner"), ephemeral=True)

    @discord.ui.button(label="Предпросмотр", emoji="👁️", style=discord.ButtonStyle.secondary, custom_id="ffcfg:v381:preview", row=1)
    async def preview(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await self.allowed(interaction):
            return
        await interaction.response.send_message("Выберите панель.", view=PanelChoiceView(self.client, "preview"), ephemeral=True)

    @discord.ui.button(label="Опубликовать панели", emoji="📌", style=discord.ButtonStyle.secondary, custom_id="ffcfg:v381:panels", row=2)
    async def panels(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await self.allowed(interaction):
            return
        await interaction.response.send_message("Выберите панель для отправки или обновления.", view=PanelChoiceView(self.client, "publish"), ephemeral=True)

    @discord.ui.button(label="Проверить Minecraft", emoji="🔌", style=discord.ButtonStyle.success, custom_id="ffcfg:v381:health", row=2)
    async def health(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await self.allowed(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        channel_id = self.client.channel_id("bridge")
        if not channel_id:
            await interaction.followup.send("Сначала выберите технический канал.", ephemeral=True)
            return
        try:
            self.client.api.set_channel_id(channel_id)
            await self.client.api.bind_channel(channel_id, GUILD_ID)
            await asyncio.sleep(1)
            data = await self.client.api.health()
            await interaction.followup.send(embed=brand_embed("Связь работает", f"Minecraft-мост: **{data.get('version', '—')}**"), ephemeral=True)
        except Exception as exc:
            await self.client.send_error(interaction, exc)

    @discord.ui.button(label="Обновить", emoji="🔄", style=discord.ButtonStyle.secondary, custom_id="ffcfg:v381:refresh", row=3)
    async def refresh(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await self.allowed(interaction):
            return
        await interaction.response.edit_message(embed=config_embed(self.client), view=self)


# Панель Discord-конфигурации управляет только интерфейсом бота:
# каналами, ролями, администраторами, текстами, баннерами и публикацией панелей.
# Экономика, цены, лимиты и сообщения игровых команд меняются только в YAML плагина.


@bot.tree.command(name="настроить_банк", description="Создать панель настройки Discord-интерфейса банка в этом канале")
@app_commands.guild_only()
async def setup_bank_config_command(interaction: discord.Interaction) -> None:
    try:
        await interaction.response.defer(ephemeral=True, thinking=True)
    except discord.NotFound:
        LOG.warning("Команда /настроить_банк пришла уже просроченной. Проверьте, что запущен только один экземпляр бота.")
        return
    if not bot.is_admin(interaction):
        await interaction.followup.send("У вас нет прав администратора банка.", ephemeral=True)
        return
    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel):
        await interaction.followup.send("Команду нужно использовать в обычном текстовом канале `config`.", ephemeral=True)
        return
    bot.state["config_channel_id"] = channel.id
    bot.state["config_message_id"] = 0
    bot.save_state()
    message = await ensure_config_panel(bot, channel)
    if message is None:
        await interaction.followup.send("Не удалось создать панель конфигурации.", ephemeral=True)
        return
    await interaction.followup.send(
        f"Панель Discord-конфигурации создана: {message.jump_url}\n"
        "Через неё настраиваются каналы, роли, администраторы, тексты, баннеры и публикация панелей. "
        "Настройки самой банковской системы остаются только на хостинге.",
        ephemeral=True,
    )


@bot.tree.command(name="перезагрузить_банк", description="Перечитать Discord-настройки бота из data/settings.json")
@app_commands.guild_only()
async def reload_host_config_command(interaction: discord.Interaction) -> None:
    try:
        await interaction.response.defer(ephemeral=True, thinking=True)
    except discord.NotFound:
        LOG.warning("Команда /перезагрузить_банк пришла уже просроченной. Проверьте, что запущен только один экземпляр бота.")
        return
    if not bot.is_admin(interaction):
        await interaction.followup.send("У вас нет прав администратора банка.", ephemeral=True)
        return
    try:
        RUNTIME.reload()
        bot.state = RUNTIME.data
        bot.state["guild_id"] = GUILD_ID
        bridge_channel = bot.channel_id("bridge")
        bot.api.set_channel_id(bridge_channel)
        await interaction.followup.send(
            "Discord-настройки бота перечитаны из `data/settings.json`. Экономические настройки плагина эта команда не меняет.",
            ephemeral=True,
        )
    except Exception as exc:
        await bot.send_error(interaction, exc)


@bot.tree.command(name="опубликовать_панели", description="Создать или обновить банковские панели по настройкам канала config")
@app_commands.guild_only()
async def publish_all_panels_command(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    if not bot.is_admin(interaction):
        await interaction.followup.send("У вас нет прав администратора банка.", ephemeral=True)
        return
    results: list[str] = []
    for key, label in PANEL_LABELS.items():
        try:
            message = await publish_panel(bot, key)
            results.append(f"✅ **{label}:** {message.jump_url}")
        except Exception as exc:
            results.append(f"❌ **{label}:** {exc}")
    await interaction.followup.send("\n".join(results), ephemeral=True)

@bot.tree.command(name="обновить_заявки", description="Принудительно проверить заявки на бизнес")
@app_commands.guild_only()
async def refresh_requests_command(interaction: discord.Interaction) -> None:
    try:
        await interaction.response.defer(ephemeral=True, thinking=True)
    except discord.NotFound:
        LOG.warning("Команда /обновить_заявки пришла уже просроченной. Проверьте, что запущен только один экземпляр бота.")
        return
    if not bot.is_admin(interaction):
        await interaction.followup.send("У вас нет прав администратора банка.", ephemeral=True)
        return
    try:
        await bot._post_business_applications()
        await interaction.followup.send("Заявки на бизнес обновлены.", ephemeral=True)
    except Exception as exc:
        await bot.send_error(interaction, exc)


@bot.event
async def on_message(message: discord.Message) -> None:
    await bot.api.handle_message(message)
    await bot.process_commands(message)


@bot.event
async def on_ready() -> None:
    LOG.info(
        "FunFernus Bank bot %s logged in as %s (%s). Discord-панель конфигурации активна; экономика банка настраивается только на хостинге.",
        BOT_PACKAGE_VERSION,
        bot.user,
        bot.user.id if bot.user else "—",
    )
    bot.state["guild_id"] = GUILD_ID
    bot.save_state()
    try:
        await ensure_config_panel(bot)
    except Exception as exc:
        LOG.warning("Не удалось восстановить панель config: %s", exc)
    bridge_channel = bot.channel_id("bridge")
    if bridge_channel:
        bot.api.set_channel_id(bridge_channel)
        try:
            await bot.api.bind_channel(bridge_channel, GUILD_ID)
            await asyncio.sleep(1)
            data = await bot.api.health()
            LOG.info("Minecraft bridge connected: FunFernusBankBridge %s", data.get("version", "—"))
        except Exception as exc:
            LOG.warning("Minecraft bridge is not ready: %s", exc)


if __name__ == "__main__":
    bot.run(TOKEN, log_handler=None)
