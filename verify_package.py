from __future__ import annotations

import ast
import py_compile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REQUIRED = [
    "bot.py", "advanced_ui.py", "discord_bus.py", "env.py",
    "runtime_settings.py", "storage.py", "banner_files.py", "requirements.txt", ".env.example",
]
EXPECTED_REQUIREMENTS = ["discord.py==2.7.1", "python-dotenv==1.1.1"]

errors: list[str] = []
for name in REQUIRED:
    if not (ROOT / name).is_file():
        errors.append(f"Нет файла: {name}")

requirements_path = ROOT / "requirements.txt"
requirements = requirements_path.read_text(encoding="utf-8").splitlines() if requirements_path.exists() else []
if requirements != EXPECTED_REQUIREMENTS:
    errors.append("requirements.txt имеет неправильное содержимое")

for name in ["bot.py", "advanced_ui.py", "discord_bus.py", "env.py", "runtime_settings.py", "storage.py", "banner_files.py"]:
    path = ROOT / name
    if not path.exists():
        continue
    try:
        py_compile.compile(str(path), doraise=True)
        ast.parse(path.read_text(encoding="utf-8"), filename=name)
    except Exception as exc:
        errors.append(f"Ошибка синтаксиса в {name}: {exc}")

texts = {
    name: (ROOT / name).read_text(encoding="utf-8")
    for name in ["bot.py", "advanced_ui.py", "discord_bus.py", "runtime_settings.py", "banner_files.py"]
}

required_markers = {
    "bot.py": [
        'BOT_PACKAGE_VERSION = "4.4.0-BUSINESS-NETWORK"',
        '@bot.tree.command(name="настроить_банк"',
        "self.add_view(ConfigPanelView(self))",
        "discord.ui.LayoutView",
        "discord.ui.MediaGallery",
        "public_panel_layout",
        "BankBannerUploadModal",
    ],
    "advanced_ui.py": [
        "business/upgrades/categories/buy",
        "business/upgrades/tax/buy",
        "FineAdminModal",
    ],
    "discord_bus.py": ["fine_admin_cancel", 'resolver("bridge")'],
    "runtime_settings.py": ["government_fines", '"admins"'],
    "banner_files.py": ["save_banner_attachment", "attachment://", "BANNER_DIRECTORY"],
}
for filename, markers in required_markers.items():
    text = texts[filename]
    for marker in markers:
        if marker not in text:
            errors.append(f"В {filename} отсутствует обязательный элемент: {marker}")

for forbidden in [
    "fine_issuers",
    "BANK_SETTING_GROUPS",
    'custom_id="ffcfg:v400:host-settings"',
    "Discord-конфиг отключён",
    "Управление баннерами через Discord удалено",
]:
    if forbidden in texts["bot.py"]:
        errors.append(f"В bot.py осталась устаревшая система: {forbidden}")

if errors:
    print("ПАКЕТ НЕ ПРОШЁЛ ПРОВЕРКУ:")
    for error in errors:
        print("-", error)
    raise SystemExit(1)

print("OK: пакет 4.4.0 прошёл проверку, сохраняет Discord config-панель и поддерживает сеть магазинов.")
