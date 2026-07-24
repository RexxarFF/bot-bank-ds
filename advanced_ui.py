from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import discord

from banner_files import apply_banner, make_banner_file
from discord_bus import ApiError


BUSINESS_TYPES = {
    "SHOP": "Магазин",
    "SERVICE": "Услуги",
    "FACTORY": "Производство",
    "OTHER": "Другое",
}
THEME_LABELS = {
    "PURPLE": "Фиолетовая",
    "BLUE": "Синяя",
    "GREEN": "Зелёная",
    "RED": "Красная",
    "YELLOW": "Жёлтая",
    "ORANGE": "Оранжевая",
    "WHITE": "Белая",
    "BLACK": "Чёрная",
}


def money(value: Any) -> str:
    try:
        return f"{int(value):,}".replace(",", " ") + " АР"
    except (TypeError, ValueError):
        return "0 АР"


def truncate(value: Any, limit: int = 1000) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def discord_time(milliseconds: Any, style: str = "R") -> str:
    try:
        return f"<t:{int(milliseconds) // 1000}:{style}>"
    except (TypeError, ValueError):
        return "—"


def russian_deadline(milliseconds: Any, timezone_name: str = "Europe/Moscow") -> str:
    try:
        zone = ZoneInfo(timezone_name)
        value = datetime.fromtimestamp(int(milliseconds) / 1000, tz=zone)
    except Exception:
        return "—"
    weekdays = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
    months = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря")
    return f"до {value:%H:%M}, {weekdays[value.weekday()]}, {value.day} {months[value.month - 1]} {value.year} г."


def fine_label(fine: dict[str, Any], fallback_number: int = 0) -> str:
    number = fine.get("displayNumber") or fine.get("number") or fallback_number
    return f"Штраф №{number or '—'}"


def parse_duration(value: str) -> int:
    text = value.strip().lower().replace(" ", "")
    units = {
        "s": 1_000, "с": 1_000,
        "m": 60_000, "м": 60_000,
        "h": 3_600_000, "ч": 3_600_000,
        "d": 86_400_000, "д": 86_400_000,
        "w": 604_800_000, "н": 604_800_000,
    }
    for suffix, multiplier in units.items():
        if text.endswith(suffix) and text[:-len(suffix)].isdigit():
            amount = int(text[:-len(suffix)])
            if amount > 0:
                return amount * multiplier
    raise ValueError("invalid duration")


def brand_name(client: Any) -> str:
    return str(client.state.get("branding", {}).get("name", "FunFernus Bank") or "FunFernus Bank")


def brand_color(client: Any) -> discord.Color:
    raw = str(client.state.get("branding", {}).get("color", "F2A93B") or "F2A93B").removeprefix("#")
    try:
        return discord.Color(int(raw, 16))
    except ValueError:
        return discord.Color(0xF2A93B)


def embed(
    client: Any,
    title: str,
    description: str = "",
    *,
    error: bool = False,
    banner_key: str = "",
) -> discord.Embed:
    result = discord.Embed(
        title=title,
        description=description,
        color=discord.Color.red() if error else brand_color(client),
        timestamp=datetime.now(timezone.utc),
    )
    result.set_footer(text=brand_name(client))
    if banner_key:
        apply_banner(result, client.state, banner_key)
    return result


async def safe_dm(user: discord.abc.User, *, title: str, description: str, client: Any) -> bool:
    try:
        await user.send(embed=embed(client, title, description))
        return True
    except (discord.Forbidden, discord.HTTPException):
        return False


async def _fetch_member(client: Any, user_id: int) -> discord.Member | None:
    guild = client.get_guild(int(client.state.get("guild_id", 0) or 0))
    if guild is None:
        return None
    member = guild.get_member(user_id)
    if member is not None:
        return member
    try:
        return await guild.fetch_member(user_id)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return None


async def grant_bank_role(client: Any, user_id: int) -> tuple[bool, str]:
    member = await _fetch_member(client, user_id)
    if member is None:
        return False, "Пользователь не найден на Discord-сервере."
    guild = member.guild
    roles = [guild.get_role(role_id) for role_id in client.role_ids("bank_access")]
    roles = [role for role in roles if role is not None]
    if not roles:
        return True, "Банковская роль не настроена, привязка сохранена без выдачи роли."
    try:
        await member.add_roles(*roles, reason="Привязка FunFernus Bank")
        return True, "Банковская роль выдана."
    except (discord.Forbidden, discord.HTTPException):
        return False, "Привязка сохранена, но бот не смог выдать банковскую роль."


async def revoke_bank_role(client: Any, user_id: int) -> tuple[bool, str]:
    member = await _fetch_member(client, user_id)
    if member is None:
        return False, "Пользователь не найден на Discord-сервере."
    guild = member.guild
    roles = [guild.get_role(role_id) for role_id in client.role_ids("bank_access")]
    roles = [role for role in roles if role is not None and role in member.roles]
    if not roles:
        return True, "Банковских ролей у пользователя нет."
    try:
        await member.remove_roles(*roles, reason="Удаление привязки FunFernus Bank")
        return True, "Банковская роль снята."
    except (discord.Forbidden, discord.HTTPException):
        return False, "Привязка удалена, но бот не смог снять банковскую роль."


class OwnedView(discord.ui.View):
    def __init__(self, client: Any, owner_id: int, *, timeout: float = 600) -> None:
        super().__init__(timeout=timeout)
        self.client = client
        self.owner_id = int(owner_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Эта панель открыта для другого пользователя.", ephemeral=True)
            return False
        return True


class LinkCodeModal(discord.ui.Modal, title="Получение доступа к банку"):
    code = discord.ui.TextInput(label="Код из Minecraft", placeholder="Код из /discordshop link", min_length=4, max_length=12)

    def __init__(self, client: Any) -> None:
        super().__init__(timeout=300)
        self.client = client

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            data = await self.client.api.call("/api/v1/link", method="POST", payload={
                "discord_id": str(interaction.user.id),
                "code": self.code.value.strip(),
            }, retries=0)
            _, role_text = await grant_bank_role(self.client, interaction.user.id)
            text = f"Minecraft: **{data.get('minecraftName', '—')}**\n{role_text}"
            await interaction.followup.send(embed=embed(self.client, "Доступ получен", text), ephemeral=True)
            await safe_dm(interaction.user, title="Банк привязан", description=text, client=self.client)
        except Exception as exc:
            await self.client.send_error(interaction, exc)


class AccessPanelView(discord.ui.View):
    def __init__(self, client: Any) -> None:
        super().__init__(timeout=None)
        self.client = client

    @discord.ui.button(label="Получить доступ", emoji="🔐", style=discord.ButtonStyle.success, custom_id="ff:v36:access")
    async def link(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(LinkCodeModal(self.client))


async def _profile(client: Any, user_id: int) -> dict[str, Any]:
    return await client.api.call("/api/v1/profile", query={"discord_id": str(user_id)}, retries=0)


async def open_personal_cabinet(interaction: discord.Interaction, client: Any, *, edit: bool = False) -> None:
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        data = await _profile(client, interaction.user.id)
        result = profile_embed(client, data)
        view = PersonalCabinetView(client, interaction.user.id, data)
        banner = make_banner_file(client.state, "bank")
        if edit and interaction.message:
            attachments = [banner] if banner is not None else []
            await interaction.message.edit(embed=result, view=view, attachments=attachments)
            await interaction.followup.send("Данные обновлены.", ephemeral=True)
        elif banner is not None:
            await interaction.followup.send(embed=result, view=view, file=banner, ephemeral=True)
        else:
            await interaction.followup.send(embed=result, view=view, ephemeral=True)
    except Exception as exc:
        await client.send_error(interaction, exc)


def profile_embed(client: Any, data: dict[str, Any]) -> discord.Embed:
    result = embed(client, "Личный кабинет", f"Minecraft: **{data.get('minecraftName', '—')}**", banner_key="bank")
    result.add_field(name="Счёт", value=f"`{data.get('accountNumber', '—')}`", inline=True)
    result.add_field(name="Баланс", value=f"**{money(data.get('balance'))}**", inline=True)
    result.add_field(name="Штрафы", value=str(data.get("unpaidFines", 0)), inline=True)
    if data.get("frozen"):
        freeze_text = truncate(data.get("freezeReason") or "Причина не указана", 500)
        if data.get("frozenUntil"):
            freeze_text += f"\nДо: {discord_time(data.get('frozenUntil'), 'F')} ({discord_time(data.get('frozenUntil'), 'R')})"
        result.add_field(name="🔒 Счёт заморожен", value=freeze_text, inline=False)
    if data.get("businessBanned"):
        result.add_field(
            name="⛔ Доступ к бизнесам заблокирован",
            value=truncate(data.get("businessBanReason") or "Причина не указана", 500),
            inline=False,
        )
    businesses = list(data.get("businesses", []) or [])
    if not businesses and data.get("business"):
        businesses = [data.get("business")]
    if businesses:
        lines = []
        for item in businesses[:3]:
            marker = "🔒 " if item.get("frozen") else ""
            suffix = " — заморожен" if item.get("frozen") else ""
            lines.append(f"{marker}**{item.get('name', '—')}** — {money(item.get('balance'))}{suffix}")
        result.add_field(name=f"Бизнесы ({len(businesses)}/{data.get('maxBusinesses', len(businesses))})", value="\n".join(lines), inline=False)
    result.set_footer(text="Чтобы получить свежий баланс, снова нажмите «Открыть банк» в банковском канале.")
    return result


class AdvancedBankPanelView(discord.ui.View):
    def __init__(self, client: Any) -> None:
        super().__init__(timeout=None)
        self.client = client

    @discord.ui.button(label="Открыть банк", emoji="🏦", style=discord.ButtonStyle.primary, custom_id="ff:v36:bank")
    async def open(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await open_personal_cabinet(interaction, self.client)


class PersonalCabinetView(OwnedView):
    def __init__(self, client: Any, owner_id: int, data: dict[str, Any]) -> None:
        super().__init__(client, owner_id)
        self.data = data
        if data.get("frozen"):
            for child in self.children:
                if getattr(child, "label", "") in {"Перевод", "Штрафы", "Казна"}:
                    child.disabled = True

    @discord.ui.button(label="Перевод", emoji="💸", style=discord.ButtonStyle.primary, row=0)
    async def transfer(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_message("Выберите получателя.", view=TransferUserView(self.client, self.owner_id), ephemeral=True)

    @discord.ui.button(label="Штрафы", emoji="📄", style=discord.ButtonStyle.secondary, row=0)
    async def fines(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await open_fines(interaction, self.client)

    @discord.ui.button(label="Казна", emoji="🏛️", style=discord.ButtonStyle.secondary, row=0)
    async def treasury(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(TreasuryAmountModal(self.client, self.owner_id))

    @discord.ui.button(label="История", emoji="🧾", style=discord.ButtonStyle.secondary, row=1)
    async def history(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await open_history(interaction, self.client)

    @discord.ui.button(label="Отвязать", emoji="🔓", style=discord.ButtonStyle.danger, row=1)
    async def unlink(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_message("Подтвердите удаление привязки.", view=UnlinkConfirmView(self.client, self.owner_id), ephemeral=True)


class TransferUserSelect(discord.ui.UserSelect):
    def __init__(self, owner: "TransferUserView") -> None:
        super().__init__(placeholder="Выберите получателя", min_values=1, max_values=1)
        self.owner = owner

    async def callback(self, interaction: discord.Interaction) -> None:
        target = self.values[0]
        if target.id == self.owner.owner_id:
            await interaction.response.send_message("Нельзя перевести деньги самому себе.", ephemeral=True)
            return
        await interaction.response.send_modal(TransferAmountModal(self.owner.client, self.owner.owner_id, target))


class TransferUserView(OwnedView):
    def __init__(self, client: Any, owner_id: int) -> None:
        super().__init__(client, owner_id)
        self.add_item(TransferUserSelect(self))


class TransferAmountModal(discord.ui.Modal, title="Сумма перевода"):
    amount = discord.ui.TextInput(label="Сумма в АР", placeholder="1000", max_length=18)

    def __init__(self, client: Any, owner_id: int, target: discord.User | discord.Member) -> None:
        super().__init__(timeout=300)
        self.client = client
        self.owner_id = owner_id
        self.target = target

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            amount = int(self.amount.value.replace(" ", ""))
            if amount <= 0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message("Укажите положительное целое число.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            data = await self.client.api.call("/api/v1/transfer", method="POST", payload={
                "discord_id": str(self.owner_id),
                "target_discord_id": str(self.target.id),
                "amount": amount,
            }, retries=0)
            text = f"Получатель: **{data.get('recipient', self.target.display_name)}**\nСумма: **{money(data.get('amount'))}**\nКомиссия: **{money(data.get('fee'))}**\nОстаток: **{money(data.get('balance'))}**"
            await interaction.followup.send(embed=embed(self.client, "Перевод выполнен", text), ephemeral=True)
            await safe_dm(self.target, title="Получен перевод", description=f"Вам перевели **{money(data.get('amount'))}**.", client=self.client)
        except Exception as exc:
            await self.client.send_error(interaction, exc)


async def open_fines(interaction: discord.Interaction, client: Any) -> None:
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        data = await client.api.call("/api/v1/fines", query={"discord_id": str(interaction.user.id)}, retries=0)
        fines = list(data.get("fines", []) or [])
        if not fines:
            await interaction.followup.send(embed=embed(client, "Штрафы", "Неоплаченных штрафов нет."), ephemeral=True)
            return
        for number, fine in enumerate(fines, start=1):
            fine["displayNumber"] = number
        result = embed(client, "Неоплаченные штрафы", f"Всего: **{len(fines)}**")
        for number, fine in enumerate(fines[:10], start=1):
            result.add_field(
                name=f"{fine_label(fine, number)} • {money(fine.get('amount'))}",
                value=f"{truncate(fine.get('reason'), 300)}\nВыдал: **{fine.get('issuedBy') or 'Система'}**\nСрок оплаты: **{russian_deadline(fine.get('dueAt'), getattr(client, 'server_timezone', 'Europe/Moscow'))}**",
                inline=False,
            )
        await interaction.followup.send(embed=result, view=FinesView(client, interaction.user.id, fines), ephemeral=True)
    except Exception as exc:
        await client.send_error(interaction, exc)


class FineSelect(discord.ui.Select):
    def __init__(self, owner: "FinesView", fines: list[dict[str, Any]]) -> None:
        options = [discord.SelectOption(label=f"{fine_label(item, index)} • {money(item.get('amount'))}", value=str(item.get("id")), description=truncate(item.get("reason"), 90)) for index, item in enumerate(fines[:25], start=1)]
        super().__init__(placeholder="Выберите штраф для оплаты", options=options)
        self.owner = owner

    async def callback(self, interaction: discord.Interaction) -> None:
        fine_id = self.values[0]
        await interaction.response.send_message("Подтвердите оплату штрафа.", view=PayFineConfirmView(self.owner.client, self.owner.owner_id, fine_id), ephemeral=True)


class FinesView(OwnedView):
    def __init__(self, client: Any, owner_id: int, fines: list[dict[str, Any]]) -> None:
        super().__init__(client, owner_id)
        self.fines = fines
        self.add_item(FineSelect(self, fines))

    @discord.ui.button(label="Оплатить все", emoji="✅", style=discord.ButtonStyle.success, row=1)
    async def pay_all(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            data = await self.client.api.call("/api/v1/fines/pay-all", method="POST", payload={"discord_id": str(self.owner_id)}, retries=0)
            await interaction.followup.send(embed=embed(self.client, "Штрафы оплачены", f"Оплачено: **{data.get('paid', 0)}**\nСписано: **{money(data.get('amount'))}**\nОстаток: **{money(data.get('balance'))}**"), ephemeral=True)
        except Exception as exc:
            await self.client.send_error(interaction, exc)


class PayFineConfirmView(OwnedView):
    def __init__(self, client: Any, owner_id: int, fine_id: str) -> None:
        super().__init__(client, owner_id)
        self.fine_id = fine_id

    @discord.ui.button(label="Оплатить", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            data = await self.client.api.call("/api/v1/fines/pay", method="POST", payload={"discord_id": str(self.owner_id), "fine_id": self.fine_id}, retries=0)
            await interaction.followup.send(embed=embed(self.client, "Штраф оплачен", f"Списано: **{money(data.get('amount'))}**\nОстаток: **{money(data.get('balance'))}**"), ephemeral=True)
        except Exception as exc:
            await self.client.send_error(interaction, exc)


class TreasuryAmountModal(discord.ui.Modal, title="Пополнение казны"):
    amount = discord.ui.TextInput(label="Сумма в АР", placeholder="1000", max_length=18)
    reason = discord.ui.TextInput(label="Комментарий", required=False, max_length=150)

    def __init__(self, client: Any, owner_id: int) -> None:
        super().__init__(timeout=300)
        self.client = client
        self.owner_id = owner_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            amount = int(self.amount.value.replace(" ", ""))
            if amount <= 0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message("Укажите положительное целое число.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            data = await self.client.api.call("/api/v1/treasury/donate", method="POST", payload={"discord_id": str(self.owner_id), "amount": amount, "reason": self.reason.value.strip()}, retries=0)
            await interaction.followup.send(embed=embed(self.client, "Казна пополнена", f"Внесено: **{money(data.get('amount'))}**\nВаш остаток: **{money(data.get('balance'))}**"), ephemeral=True)
        except Exception as exc:
            await self.client.send_error(interaction, exc)


async def open_history(interaction: discord.Interaction, client: Any) -> None:
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        data = await client.api.call("/api/v1/history", query={"discord_id": str(interaction.user.id), "limit": 15}, retries=0)
        rows = list(data.get("transactions", []) or [])
        result = embed(client, "История операций", f"Показано: **{len(rows)}**")
        for row in rows[:15]:
            result.add_field(name=f"{row.get('type', 'Операция')} • {money(row.get('amount'))}", value=f"{row.get('from', '—')} → {row.get('to', '—')}\n{discord_time(row.get('timestamp'))}", inline=False)
        await interaction.followup.send(embed=result, ephemeral=True)
    except Exception as exc:
        await client.send_error(interaction, exc)


class UnlinkConfirmView(OwnedView):
    @discord.ui.button(label="Удалить привязку", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await self.client.api.call("/api/v1/unlink", method="POST", payload={"discord_id": str(self.owner_id)}, retries=0)
            _, text = await revoke_bank_role(self.client, self.owner_id)
            await interaction.followup.send(embed=embed(self.client, "Привязка удалена", text), ephemeral=True)
        except Exception as exc:
            await self.client.send_error(interaction, exc)


BUSINESS_STATUS_LABELS = {
    "PENDING": "заявка находится на рассмотрении",
    "APPROVED": "бизнес открыт",
    "REJECTED": "предыдущая заявка отклонена",
    "FROZEN": "бизнес заморожен",
    "CLOSED": "бизнес закрыт",
    "SUSPENDED": "работа бизнеса приостановлена",
    "BLOCKED": "бизнес заблокирован",
}


class BusinessApplicationPanelView(discord.ui.View):
    def __init__(self, client: Any) -> None:
        super().__init__(timeout=None)
        self.client = client

    @discord.ui.button(label="Подать заявку", emoji="📝", style=discord.ButtonStyle.primary, custom_id="ff:v36:business-application")
    async def apply(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            profile = await _profile(self.client, interaction.user.id)
            if profile.get("businessBanned"):
                await interaction.followup.send(
                    embed=embed(
                        self.client,
                        "Доступ к бизнесам заблокирован",
                        f"Причина: **{profile.get('businessBanReason') or 'не указана'}**",
                        error=True,
                    ),
                    ephemeral=True,
                )
                return
            businesses = list(profile.get("businesses", []) or [])
            max_businesses = max(1, int(profile.get("maxBusinesses", 1) or 1))
            if len(businesses) >= max_businesses:
                names = "\n".join(f"• **{item.get('name', 'Без названия')}**" for item in businesses)
                await interaction.followup.send(
                    embed=embed(
                        self.client,
                        "Достигнут лимит бизнесов",
                        f"У вас уже **{len(businesses)} из {max_businesses}** бизнесов.\n{names}",
                    ),
                    ephemeral=True,
                )
                return
            await interaction.followup.send(
                embed=embed(
                    self.client,
                    "Открытие бизнеса",
                    f"Сейчас открыто: **{len(businesses)} из {max_businesses}**.\n"
                    "Нажмите кнопку ниже, чтобы заполнить заявку на новый бизнес.",
                ),
                view=BusinessNewApplicationView(self.client, interaction.user.id),
                ephemeral=True,
            )
        except Exception as exc:
            await self.client.send_error(interaction, exc)


class BusinessNewApplicationView(OwnedView):
    @discord.ui.button(label="Заполнить заявку", emoji="📝", style=discord.ButtonStyle.primary)
    async def open_application(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(BusinessApplicationModal(self.client))


class BusinessApplicationModal(discord.ui.Modal, title="Заявка на бизнес"):
    business_name = discord.ui.TextInput(label="Название бизнеса", min_length=3, max_length=50)
    business_type = discord.ui.TextInput(label="Тип: SHOP / SERVICE / FACTORY / OTHER", placeholder="SHOP", max_length=20)
    description = discord.ui.TextInput(label="Описание", style=discord.TextStyle.paragraph, min_length=10, max_length=700)
    place = discord.ui.TextInput(label="Планируемое место", min_length=3, max_length=150)

    def __init__(self, client: Any) -> None:
        super().__init__(timeout=600)
        self.client = client

    async def on_submit(self, interaction: discord.Interaction) -> None:
        kind = self.business_type.value.strip().upper()
        if kind not in BUSINESS_TYPES:
            await interaction.response.send_message("Тип должен быть: SHOP, SERVICE, FACTORY или OTHER.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            data = await self.client.api.call("/api/v1/business/applications/create", method="POST", payload={
                "discord_id": str(interaction.user.id),
                "business_name": self.business_name.value.strip(),
                "type": kind,
                "description": self.description.value.strip(),
                "place": self.place.value.strip(),
            }, retries=0)
            text = f"Название: **{data.get('businessName')}**\nТип: **{BUSINESS_TYPES.get(data.get('type'), data.get('type'))}**\nРегистрационный взнос: **{money(data.get('registrationFee'))}**\nСтатус: **отправлена на рассмотрение**"
            await interaction.followup.send(embed=embed(self.client, "Заявка отправлена", text), ephemeral=True)
            await safe_dm(interaction.user, title="Заявка на бизнес отправлена", description=text, client=self.client)
        except Exception as exc:
            await self.client.send_error(interaction, exc)


def business_embed(client: Any, data: dict[str, Any]) -> discord.Embed:
    result = embed(client, "Управление бизнесом", f"**{data.get('name', '—')}**", banner_key="business")
    result.add_field(name="Баланс бизнеса", value=f"**{money(data.get('balance'))}**", inline=True)
    result.add_field(name="Личный баланс", value=f"**{money(data.get('personalBalance'))}**", inline=True)
    result.add_field(name="Продажи", value=f"Сегодня: **{data.get('todaySales', 0)}**\nВсего: **{data.get('totalSales', 0)}**", inline=True)
    result.add_field(name="Выручка", value=f"Сегодня: **{money(data.get('todayRevenue'))}**\nВсего: **{money(data.get('totalRevenue'))}**", inline=True)
    result.add_field(name="Склад", value=f"Заканчиваются: **{data.get('lowStock', 0)}**\nПустые: **{data.get('emptyStock', 0)}**", inline=True)
    terminal = data.get("terminal") or {}
    if terminal.get("placed"):
        result.add_field(name="Терминал бизнеса", value=f"`{terminal.get('world')}` • **{terminal.get('x')}, {terminal.get('y')}, {terminal.get('z')}**", inline=False)
    else:
        result.add_field(name="Терминал бизнеса", value="Не установлен. Бизнес не показывается в игровом каталоге.", inline=False)
    result.add_field(
        name="Игровой каталог",
        value=(
            "Оформление открывается бесплатно за продажи и настраивается в Minecraft: `/biz design`."
        ),
        inline=False,
    )
    result.set_footer(text="Чтобы обновить баланс, продажи и склад, снова нажмите «Открыть управление бизнесом».")
    return result


def _business_query(user_id: int, business_id: str) -> dict[str, str]:
    return {"discord_id": str(user_id), "business_id": str(business_id)}


def _business_payload(user_id: int, business_id: str, **values: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"discord_id": str(user_id), "business_id": str(business_id)}
    result.update(values)
    return result


class BusinessChoiceSelect(discord.ui.Select):
    def __init__(self, owner: "BusinessChoiceView", businesses: list[dict[str, Any]]) -> None:
        options = [
            discord.SelectOption(
                label=truncate(("🔒 " if item.get("frozen") else "") + (item.get("name") or "Без названия"), 100),
                value=str(item.get("id")),
                description=truncate(
                    (f"Заморожен • {item.get('freezeReason') or 'причина не указана'}" if item.get("frozen") else f"Баланс: {money(item.get('balance'))} • {item.get('type', 'BUSINESS')}"),
                    100,
                ),
            )
            for item in businesses[:25]
            if item.get("id")
        ]
        super().__init__(placeholder="Выберите бизнес", options=options, min_values=1, max_values=1)
        self.owner = owner

    async def callback(self, interaction: discord.Interaction) -> None:
        await open_business_dashboard(interaction, self.owner.client, business_id=self.values[0])


class BusinessChoiceView(OwnedView):
    def __init__(self, client: Any, owner_id: int, businesses: list[dict[str, Any]]) -> None:
        super().__init__(client, owner_id)
        self.add_item(BusinessChoiceSelect(self, businesses))


async def open_business_dashboard(interaction: discord.Interaction, client: Any, *, business_id: str | None = None) -> None:
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        if not business_id:
            profile = await _profile(client, interaction.user.id)
            if profile.get("businessBanned"):
                await interaction.followup.send(
                    embed=embed(
                        client,
                        "Доступ к бизнесам заблокирован",
                        f"Причина: **{profile.get('businessBanReason') or 'не указана'}**",
                        error=True,
                    ),
                    ephemeral=True,
                )
                return
            businesses = list(profile.get("businesses", []) or [])
            if not businesses:
                await interaction.followup.send(
                    embed=embed(client, "Управление бизнесом", "У вас нет активных бизнесов."),
                    ephemeral=True,
                )
                return
            if len(businesses) > 1:
                text = "\n".join(
                    f"• {'🔒 ' if item.get('frozen') else ''}**{item.get('name', 'Без названия')}** — "
                    + (f"заморожен: {item.get('freezeReason') or 'причина не указана'}" if item.get("frozen") else money(item.get("balance")))
                    for item in businesses
                )
                await interaction.followup.send(
                    embed=embed(client, "Выберите бизнес", text),
                    view=BusinessChoiceView(client, interaction.user.id, businesses),
                    ephemeral=True,
                )
                return
            business_id = str(businesses[0].get("id") or "")

        data = await client.api.call(
            "/api/v1/business",
            query=_business_query(interaction.user.id, business_id),
            retries=0,
        )
        result = business_embed(client, data)
        view = BusinessDashboardView(client, interaction.user.id, data, business_id)
        banner = make_banner_file(client.state, "business")
        if banner is not None:
            await interaction.followup.send(embed=result, view=view, file=banner, ephemeral=True)
        else:
            await interaction.followup.send(embed=result, view=view, ephemeral=True)
    except Exception as exc:
        await client.send_error(interaction, exc)


class AdvancedBusinessPanelView(discord.ui.View):
    def __init__(self, client: Any) -> None:
        super().__init__(timeout=None)
        self.client = client

    @discord.ui.button(label="Открыть управление бизнесом", emoji="📊", style=discord.ButtonStyle.primary, custom_id="ff:v36:business")
    async def business(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await open_business_dashboard(interaction, self.client)

class BusinessDashboardView(OwnedView):
    def __init__(self, client: Any, owner_id: int, data: dict[str, Any], business_id: str) -> None:
        super().__init__(client, owner_id)
        self.data = data
        self.business_id = business_id

    @discord.ui.button(label="Положить деньги", emoji="➕", style=discord.ButtonStyle.success, row=0)
    async def deposit(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(BusinessMoneyModal(self.client, self.owner_id, self.business_id, "deposit"))

    @discord.ui.button(label="Снять деньги", emoji="➖", style=discord.ButtonStyle.danger, row=0)
    async def withdraw(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(BusinessMoneyModal(self.client, self.owner_id, self.business_id, "withdraw"))

    @discord.ui.button(label="Товары", emoji="📦", style=discord.ButtonStyle.secondary, row=0)
    async def products(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await open_products(interaction, self.client, self.business_id)

    @discord.ui.button(label="Категории", emoji="🗂️", style=discord.ButtonStyle.secondary, row=1)
    async def categories(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await open_categories(interaction, self.client, self.business_id)

    @discord.ui.button(label="Оформление по продажам", emoji="🎨", style=discord.ButtonStyle.primary, row=1)
    async def theme(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await open_theme(interaction, self.client, self.business_id, self.data)

    @discord.ui.button(label="Улучшения", emoji="⬆️", style=discord.ButtonStyle.success, row=2)
    async def upgrades(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await open_business_upgrades(interaction, self.client, self.business_id, self.data)


class BusinessMoneyModal(discord.ui.Modal):
    amount = discord.ui.TextInput(label="Сумма в АР", placeholder="1000", max_length=18)

    def __init__(self, client: Any, owner_id: int, business_id: str, action: str) -> None:
        super().__init__(title="Пополнить бизнес" if action == "deposit" else "Снять с бизнеса", timeout=300)
        self.client = client
        self.owner_id = owner_id
        self.business_id = business_id
        self.action = action

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            amount = int(self.amount.value.replace(" ", ""))
            if amount <= 0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message("Укажите положительное целое число.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        path = "/api/v1/business/deposit" if self.action == "deposit" else "/api/v1/business/withdraw"
        try:
            data = await self.client.api.call(path, method="POST", payload=_business_payload(self.owner_id, self.business_id, amount=amount), retries=0)
            title = "Бизнес пополнен" if self.action == "deposit" else "Деньги сняты с бизнеса"
            text = f"Сумма: **{money(data.get('amount'))}**\nБаланс бизнеса: **{money(data.get('businessBalance'))}**\nЛичный баланс: **{money(data.get('personalBalance'))}**\n\nЧтобы увидеть новые значения в кабинете, откройте управление бизнесом заново."
            await interaction.followup.send(embed=embed(self.client, title, text), ephemeral=True)
            await safe_dm(interaction.user, title=title, description=text, client=self.client)
        except Exception as exc:
            await self.client.send_error(interaction, exc)


async def open_products(interaction: discord.Interaction, client: Any, business_id: str) -> None:
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        data = await client.api.call("/api/v1/business", query=_business_query(interaction.user.id, business_id), retries=0)
        products = list(data.get("productList", []) or [])
        if not products:
            await interaction.followup.send(embed=embed(client, "Товары бизнеса", "Товаров пока нет. Добавление и пополнение выполняются только в Minecraft через терминал."), ephemeral=True)
            return
        result = embed(client, "Товары бизнеса", "Название определяется предметом в Minecraft автоматически. Здесь можно изменить только цену, размер набора и видимость. Пополнение склада выполняется в Minecraft.")
        await interaction.followup.send(embed=result, view=ProductsView(client, interaction.user.id, business_id, products), ephemeral=True)
    except Exception as exc:
        await client.send_error(interaction, exc)


class ProductSelect(discord.ui.Select):
    def __init__(self, owner: "ProductsView", products: list[dict[str, Any]]) -> None:
        options = [discord.SelectOption(label=truncate(p.get("name"), 100), value=str(p.get("id")), description=truncate(f"{money(p.get('price'))} • склад {p.get('stock')} • {'виден' if p.get('enabled') else 'скрыт'}", 100)) for p in products[:25]]
        super().__init__(placeholder="Выберите товар", options=options)
        self.owner = owner

    async def callback(self, interaction: discord.Interaction) -> None:
        product = next((p for p in self.owner.products if str(p.get("id")) == self.values[0]), None)
        if product is None:
            await interaction.response.send_message("Товар не найден.", ephemeral=True)
            return
        await interaction.response.send_modal(ProductEditModal(self.owner.client, self.owner.owner_id, self.owner.business_id, product))


class ProductsView(OwnedView):
    def __init__(self, client: Any, owner_id: int, business_id: str, products: list[dict[str, Any]]) -> None:
        super().__init__(client, owner_id)
        self.business_id = business_id
        self.products = products
        self.add_item(ProductSelect(self, products))


class ProductEditModal(discord.ui.Modal, title="Изменить товар"):
    price = discord.ui.TextInput(label="Цена", max_length=18)
    bundle = discord.ui.TextInput(label="Количество в наборе", max_length=6)
    enabled = discord.ui.TextInput(label="Показывать: да / нет", max_length=5)

    def __init__(self, client: Any, owner_id: int, business_id: str, product: dict[str, Any]) -> None:
        super().__init__(timeout=300)
        self.client = client
        self.owner_id = owner_id
        self.business_id = business_id
        self.product = product
        self.price.default = str(product.get("price", 0))
        self.bundle.default = str(product.get("amount", 1))
        self.enabled.default = "да" if product.get("enabled") else "нет"

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            price = int(self.price.value.replace(" ", ""))
            bundle = int(self.bundle.value.replace(" ", ""))
            if price <= 0 or bundle <= 0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message("Цена и количество должны быть положительными целыми числами.", ephemeral=True)
            return
        enabled_raw = self.enabled.value.strip().lower()
        if enabled_raw not in {"да", "нет", "true", "false", "1", "0"}:
            await interaction.response.send_message("В поле видимости укажите `да` или `нет`.", ephemeral=True)
            return
        enabled = enabled_raw in {"да", "true", "1"}
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await self.client.api.call("/api/v1/business/products/edit", method="POST", payload={
                "discord_id": str(self.owner_id),
                "business_id": self.business_id,
                "product_id": str(self.product.get("id")),
                # Название не редактируется пользователем, но передаётся мосту
                # без изменений для совместимости с текущим API.
                "name": str(self.product.get("name", "")),
                "price": price,
                "bundle_amount": bundle,
                "enabled": enabled,
            }, retries=0)
            await interaction.followup.send("Товар обновлён. Название и склад не изменялись.", ephemeral=True)
        except Exception as exc:
            await self.client.send_error(interaction, exc)


async def open_categories(interaction: discord.Interaction, client: Any, business_id: str) -> None:
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        data = await client.api.call("/api/v1/business", query=_business_query(interaction.user.id, business_id), retries=0)
        categories = list(data.get("categoryList", []) or [])
        text = "\n".join(f"• **{item.get('name')}** — `{item.get('id')}`" for item in categories) or "Категорий пока нет."
        await interaction.followup.send(embed=embed(client, "Категории", text), view=CategoriesView(client, interaction.user.id, business_id, categories), ephemeral=True)
    except Exception as exc:
        await client.send_error(interaction, exc)


class CategoriesView(OwnedView):
    def __init__(self, client: Any, owner_id: int, business_id: str, categories: list[dict[str, Any]]) -> None:
        super().__init__(client, owner_id)
        self.business_id = business_id
        self.categories = categories

    @discord.ui.button(label="Создать", emoji="➕", style=discord.ButtonStyle.success)
    async def create(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(CategoryCreateModal(self.client, self.owner_id, self.business_id))

    @discord.ui.button(label="Переименовать", emoji="✏️", style=discord.ButtonStyle.secondary)
    async def rename(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(CategoryRenameModal(self.client, self.owner_id, self.business_id))

    @discord.ui.button(label="Удалить", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def delete(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(CategoryDeleteModal(self.client, self.owner_id, self.business_id))


class CategoryCreateModal(discord.ui.Modal, title="Создать категорию"):
    name = discord.ui.TextInput(label="Название", min_length=2, max_length=50)
    def __init__(self, client: Any, owner_id: int, business_id: str) -> None:
        super().__init__(timeout=300); self.client = client; self.owner_id = owner_id; self.business_id = business_id
    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await self.client.api.call("/api/v1/business/categories/create", method="POST", payload=_business_payload(self.owner_id, self.business_id, name=self.name.value.strip()), retries=0)
            await interaction.followup.send("Категория создана.", ephemeral=True)
        except Exception as exc: await self.client.send_error(interaction, exc)


class CategoryRenameModal(discord.ui.Modal, title="Переименовать категорию"):
    category_id = discord.ui.TextInput(label="ID категории", max_length=80)
    name = discord.ui.TextInput(label="Новое название", min_length=2, max_length=50)
    def __init__(self, client: Any, owner_id: int, business_id: str) -> None:
        super().__init__(timeout=300); self.client = client; self.owner_id = owner_id; self.business_id = business_id
    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await self.client.api.call("/api/v1/business/categories/rename", method="POST", payload=_business_payload(self.owner_id, self.business_id, category_id=self.category_id.value.strip(), name=self.name.value.strip()), retries=0)
            await interaction.followup.send("Категория переименована.", ephemeral=True)
        except Exception as exc: await self.client.send_error(interaction, exc)


class CategoryDeleteModal(discord.ui.Modal, title="Удалить категорию"):
    category_id = discord.ui.TextInput(label="ID категории", max_length=80)
    def __init__(self, client: Any, owner_id: int, business_id: str) -> None:
        super().__init__(timeout=300); self.client = client; self.owner_id = owner_id; self.business_id = business_id
    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await self.client.api.call("/api/v1/business/categories/delete", method="POST", payload=_business_payload(self.owner_id, self.business_id, category_id=self.category_id.value.strip()), retries=0)
            await interaction.followup.send("Категория удалена.", ephemeral=True)
        except Exception as exc: await self.client.send_error(interaction, exc)


def _tax_percent(value: Any) -> str:
    try:
        number = float(value)
        return f"{number:.2f}".rstrip("0").rstrip(".") + "%"
    except (TypeError, ValueError):
        return "—"


async def open_business_upgrades(interaction: discord.Interaction, client: Any, business_id: str, data: dict[str, Any] | None = None) -> None:
    try:
        if data is None:
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True, thinking=True)
            data = await client.api.call("/api/v1/business", query=_business_query(interaction.user.id, business_id), retries=0)
        upgrades = dict(data.get("upgrades") or {})
        category_level = int(upgrades.get("categoryLevel", 0) or 0)
        max_category_level = int(upgrades.get("maxCategoryLevel", 0) or 0)
        tax_level = int(upgrades.get("taxLevel", 0) or 0)
        max_tax_level = int(upgrades.get("maxTaxLevel", 0) or 0)
        category_cost = int(upgrades.get("categoryNextCost", 0) or 0)
        tax_cost = int(upgrades.get("taxNextCost", 0) or 0)

        message = embed(
            client,
            "Улучшения бизнеса",
            "Улучшения оплачиваются с личного банковского счёта, как и в Minecraft через `/biz upgrade`.",
        )
        category_text = (
            f"Уровень: **{category_level}/{max_category_level}**\n"
            f"Доступно категорий: **{upgrades.get('categoryLimit', 0)}**\n"
            + ("Достигнут максимальный уровень." if category_level >= max_category_level else f"Следующее улучшение: **{money(category_cost)}**")
        )
        tax_text = (
            f"Уровень: **{tax_level}/{max_tax_level}**\n"
            f"Текущий налог с продажи: **{_tax_percent(upgrades.get('effectiveTaxPercent'))}**\n"
            + ("Достигнут максимальный уровень." if tax_level >= max_tax_level else f"Следующее улучшение: **{money(tax_cost)}**")
        )
        message.add_field(name="Лимит категорий", value=category_text, inline=False)
        message.add_field(name="Снижение налога", value=tax_text, inline=False)
        message.set_footer(text="После покупки снова откройте управление бизнесом, чтобы увидеть свежие данные.")
        view = BusinessUpgradeView(client, interaction.user.id, business_id, upgrades)
        if interaction.response.is_done():
            await interaction.followup.send(embed=message, view=view, ephemeral=True)
        else:
            await interaction.response.send_message(embed=message, view=view, ephemeral=True)
    except Exception as exc:
        await client.send_error(interaction, exc)


class BusinessUpgradeView(OwnedView):
    def __init__(self, client: Any, owner_id: int, business_id: str, upgrades: dict[str, Any]) -> None:
        super().__init__(client, owner_id)
        self.business_id = business_id
        self.upgrades = upgrades
        self.category_upgrade.disabled = int(upgrades.get("categoryLevel", 0) or 0) >= int(upgrades.get("maxCategoryLevel", 0) or 0)
        self.tax_upgrade.disabled = int(upgrades.get("taxLevel", 0) or 0) >= int(upgrades.get("maxTaxLevel", 0) or 0)

    @discord.ui.button(label="Улучшить категории", emoji="🗂️", style=discord.ButtonStyle.success)
    async def category_upgrade(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._buy(interaction, "/api/v1/business/upgrades/categories/buy", "Лимит категорий улучшен")

    @discord.ui.button(label="Снизить налог", emoji="📉", style=discord.ButtonStyle.primary)
    async def tax_upgrade(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._buy(interaction, "/api/v1/business/upgrades/tax/buy", "Налог бизнеса улучшен")

    async def _buy(self, interaction: discord.Interaction, path: str, title: str) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            data = await self.client.api.call(path, method="POST", payload=_business_payload(self.owner_id, self.business_id), retries=0)
            if data.get("type") == "CATEGORIES":
                details = (
                    f"Списано: **{money(data.get('cost'))}**\n"
                    f"Новый уровень: **{data.get('categoryLevel')}/{data.get('maxCategoryLevel')}**\n"
                    f"Новый лимит категорий: **{data.get('categoryLimit')}**"
                )
            else:
                details = (
                    f"Списано: **{money(data.get('cost'))}**\n"
                    f"Новый уровень: **{data.get('taxLevel')}/{data.get('maxTaxLevel')}**\n"
                    f"Налог с продажи: **{_tax_percent(data.get('effectiveTaxPercent'))}**"
                )
            details += f"\nЛичный баланс: **{money(data.get('personalBalance'))}**\n\nОткройте управление бизнесом заново, чтобы обновить кабинет."
            await interaction.followup.send(embed=embed(self.client, title, details), ephemeral=True)
        except Exception as exc:
            await self.client.send_error(interaction, exc)


async def open_theme(interaction: discord.Interaction, client: Any, business_id: str, data: dict[str, Any] | None = None) -> None:
    message = embed(
        client,
        "Оформление бизнеса",
        "Цвета больше не покупаются. Они открываются автоматически за общее количество продаж.\n\n"
        "Настройка выполняется в Minecraft:\n"
        "`/biz design list` — посмотреть открытые цвета и следующие достижения;\n"
        "`/biz design set <цвет1> <цвет2> ...` — выбрать до настроенного количества цветов и создать градиент.\n\n"
        "Пороги, доступные цвета и максимальное количество цветов меняются на хостинге в `plugins/FunFernusBank/design.yml`.",
    )
    if interaction.response.is_done():
        await interaction.followup.send(embed=message, ephemeral=True)
    else:
        await interaction.response.send_message(embed=message, ephemeral=True)




class GovernmentFinePanelView(discord.ui.View):
    def __init__(self, client: Any) -> None:
        super().__init__(timeout=None)
        self.client = client

    @discord.ui.button(label="Выдать штраф", emoji="📄", style=discord.ButtonStyle.danger, custom_id="ff:v36:government-fine")
    async def issue(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not self.client.can_issue_fine(interaction):
            await interaction.response.send_message("Выдавать штрафы могут только администраторы банка.", ephemeral=True)
            return
        await interaction.response.send_message("Выберите игрока, которому нужно выдать штраф.", view=FineTargetView(self.client, interaction.user.id), ephemeral=True)

    @discord.ui.button(label="Отменить / погасить", emoji="🛡️", style=discord.ButtonStyle.secondary, custom_id="ff:v36:government-fine-admin")
    async def manage(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not self.client.is_admin(interaction):
            await interaction.response.send_message("Это действие доступно только администраторам банка.", ephemeral=True)
            return
        await interaction.response.send_modal(FineAdminModal(self.client))


class FineTargetSelect(discord.ui.UserSelect):
    def __init__(self, owner: "FineTargetView") -> None:
        super().__init__(placeholder="Выберите игрока", min_values=1, max_values=1)
        self.owner = owner
    async def callback(self, interaction: discord.Interaction) -> None:
        target = self.values[0]
        if target.id == interaction.user.id:
            await interaction.response.send_message("Нельзя выдать штраф самому себе.", ephemeral=True)
            return
        await interaction.response.send_modal(FineIssueModal(self.owner.client, target))


class FineTargetView(OwnedView):
    def __init__(self, client: Any, owner_id: int) -> None:
        super().__init__(client, owner_id)
        self.add_item(FineTargetSelect(self))


class FineIssueModal(discord.ui.Modal, title="Выдать штраф"):
    amount = discord.ui.TextInput(label="Сумма", placeholder="1000", max_length=18)
    duration = discord.ui.TextInput(label="Срок", placeholder="30с, 15м, 2ч, 7д или 1н", max_length=12)
    reason = discord.ui.TextInput(label="Причина", style=discord.TextStyle.paragraph, min_length=3, max_length=200)

    def __init__(self, client: Any, target: discord.User | discord.Member) -> None:
        super().__init__(timeout=300)
        self.client = client
        self.target = target

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not self.client.can_issue_fine(interaction):
            await interaction.response.send_message("Выдавать штрафы могут только администраторы банка.", ephemeral=True)
            return
        try:
            amount = int(self.amount.value.replace(" ", ""))
            duration_ms = parse_duration(self.duration.value)
            if amount <= 0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message("Укажите положительную сумму и срок: 30с, 15м, 2ч, 7д или 1н.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            data = await self.client.api.call("/api/v1/fines/issue", method="POST", payload={
                "discord_id": str(interaction.user.id),
                "target_discord_id": str(self.target.id),
                "amount": amount,
                "duration_ms": duration_ms,
                "reason": self.reason.value.strip(),
                "issuer_name": interaction.user.display_name,
            }, retries=0)
            base_text = f"Игрок: **{data.get('target')}**\nСумма: **{money(data.get('amount'))}**\nПричина: **{data.get('reason')}**\nСрок оплаты: **{russian_deadline(data.get('dueAt'), getattr(self.client, 'server_timezone', 'Europe/Moscow'))}**"
            admin_text = base_text + f"\nТехнический ID: `{data.get('fineId')}`"
            player_text = f"Сумма: **{money(data.get('amount'))}**\nПричина: **{data.get('reason')}**\nСрок оплаты: **{russian_deadline(data.get('dueAt'), getattr(self.client, 'server_timezone', 'Europe/Moscow'))}**"
            await interaction.followup.send(embed=embed(self.client, "Штраф выдан", admin_text), ephemeral=True)
            await safe_dm(self.target, title="Вам выдан штраф", description=player_text, client=self.client)
        except Exception as exc:
            await self.client.send_error(interaction, exc)


class FineAdminModal(discord.ui.Modal, title="Управление штрафом"):
    fine_id = discord.ui.TextInput(label="ID штрафа", placeholder="FINE-000001", max_length=80)
    action = discord.ui.TextInput(label="Действие: отменить / погасить", placeholder="отменить", max_length=20)
    reason = discord.ui.TextInput(label="Причина", style=discord.TextStyle.paragraph, min_length=3, max_length=200)

    def __init__(self, client: Any) -> None:
        super().__init__(timeout=300)
        self.client = client

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not self.client.is_admin(interaction):
            await interaction.response.send_message("У вас нет прав администратора банка.", ephemeral=True)
            return
        action = self.action.value.strip().lower()
        if action not in {"отменить", "cancel", "погасить", "waive"}:
            await interaction.response.send_message("Укажите действие `отменить` или `погасить`.", ephemeral=True)
            return
        path = "/api/v1/admin/fines/cancel" if action in {"отменить", "cancel"} else "/api/v1/admin/fines/waive"
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            data = await self.client.api.call(path, method="POST", payload={
                "fine_id": self.fine_id.value.strip(),
                "actor": interaction.user.display_name,
                "reason": self.reason.value.strip(),
            }, retries=0)
            title = "Штраф отменён" if "cancel" in path else "Штраф погашен"
            await interaction.followup.send(embed=embed(self.client, title, f"ID: `{data.get('fineId')}`\nИгрок: **{data.get('target')}**\nСумма: **{money(data.get('amount'))}**"), ephemeral=True)
        except Exception as exc:
            await self.client.send_error(interaction, exc)
