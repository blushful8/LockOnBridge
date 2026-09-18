from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Strings:
    subtitle: str
    bridge_enabled: str
    tip: str
    http_port: str
    open_logs: str
    hide_to_tray: str
    check_updates: str
    uninstall: str
    quit: str
    language: str
    lang_en: str
    lang_uk: str
    badge_on: str
    badge_off: str
    badge_idle: str
    badge_active: str
    status_disabled: str
    status_enabled_waiting: str
    status_idle: str
    status_active: str
    status_stopping: str
    status_port_saved: str
    tray_open: str
    tray_disable: str
    tray_quit: str
    tray_need_enable: str
    uninstall_confirm: str
    update_checking: str
    update_up_to_date: str
    update_available_title: str
    update_available_body: str
    update_confirm: str
    update_cancel: str
    update_downloading: str
    update_failed: str
    update_dev_only: str
    update_restarting: str


EN = Strings(
    subtitle="OCR companion for LockOn (War Thunder)",
    bridge_enabled="Bridge enabled",
    tip=(
        "When enabled: starts with Windows, wakes with War Thunder, "
        "stops when the game exits.\n"
        "When disabled: no autostart, no background process, no CPU/RAM use."
    ),
    http_port="HTTP port",
    open_logs="Open logs",
    hide_to_tray="Hide to tray",
    check_updates="Check for updates",
    uninstall="Uninstall…",
    quit="Quit",
    language="Language",
    lang_en="English",
    lang_uk="Ukrainian",
    badge_on="ON",
    badge_off="OFF",
    badge_idle="IDLE",
    badge_active="ACTIVE",
    status_disabled="Disabled — no autostart, no background work",
    status_enabled_waiting="Enabled — waiting for War Thunder",
    status_idle="Idle — checks every {seconds:.0f}s",
    status_active="War Thunder — Bridge active",
    status_stopping="Stopping…",
    status_port_saved="Port saved ({port})",
    tray_open="Open",
    tray_disable="Disable Bridge",
    tray_quit="Quit",
    tray_need_enable="Enable Bridge first. While disabled there is nothing to keep in the tray.",
    uninstall_confirm=(
        "Remove LockOn Bridge completely?\n\n"
        "This disables autostart, stops the agent, and deletes local files."
    ),
    update_checking="Checking for updates…",
    update_up_to_date="You already have the latest version ({version}).",
    update_available_title="Update available",
    update_available_body=(
        "Version {latest} is available (you have {current}).\n\n"
        "Download and install the update now?"
    ),
    update_confirm="Update",
    update_cancel="Cancel",
    update_downloading="Downloading update…",
    update_failed="Could not update: {error}",
    update_dev_only="Automatic updates work with LockOnBridge.exe builds. Run the packaged release to update.",
    update_restarting="Update downloaded. LockOn Bridge will restart.",
)

UK = Strings(
    subtitle="OCR-супутник для LockOn (War Thunder)",
    bridge_enabled="Bridge увімкнено",
    tip=(
        "Коли увімкнено: автозапуск із Windows, робота з War Thunder, "
        "зупинка після виходу з гри.\n"
        "Коли вимкнено: без автозапуску, без фонового процесу, без навантаження на процесор і памʼять."
    ),
    http_port="Порт HTTP",
    open_logs="Відкрити журнали",
    hide_to_tray="Згорнути в трей",
    check_updates="Перевірити оновлення",
    uninstall="Видалити…",
    quit="Вийти",
    language="Мова",
    lang_en="English",
    lang_uk="Українська",
    badge_on="УВІМК.",
    badge_off="ВИМК.",
    badge_idle="ОЧІКУВАННЯ",
    badge_active="АКТИВНИЙ",
    status_disabled="Вимкнено — немає автозапуску й фонової роботи",
    status_enabled_waiting="Увімкнено — очікування War Thunder",
    status_idle="Очікування — перевірка кожні {seconds:.0f} с",
    status_active="War Thunder — Bridge активний",
    status_stopping="Зупинка…",
    status_port_saved="Порт збережено ({port})",
    tray_open="Відкрити",
    tray_disable="Вимкнути Bridge",
    tray_quit="Вийти",
    tray_need_enable="Спочатку увімкніть Bridge. Поки він вимкнений, у треї тримати нічого.",
    uninstall_confirm=(
        "Повністю видалити LockOn Bridge?\n\n"
        "Автозапуск буде вимкнено, агент зупинено, локальні файли видалено."
    ),
    update_checking="Перевірка оновлень…",
    update_up_to_date="У вас уже остання версія ({version}).",
    update_available_title="Доступне оновлення",
    update_available_body=(
        "Доступна версія {latest} (у вас {current}).\n\n"
        "Завантажити й установити оновлення зараз?"
    ),
    update_confirm="Оновити",
    update_cancel="Скасувати",
    update_downloading="Завантаження оновлення…",
    update_failed="Не вдалося оновити: {error}",
    update_dev_only="Автоматичні оновлення працюють із зібраним LockOnBridge.exe. Запустіть релізну збірку.",
    update_restarting="Оновлення завантажено. LockOn Bridge перезапуститься.",
)


def strings_for(language: str) -> Strings:
    return UK if language.lower().startswith("uk") else EN


def detect_system_language() -> str:
    try:
        import locale

        raw = locale.getdefaultlocale()[0] or ""
    except Exception:  # noqa: BLE001
        raw = ""
    if raw.lower().startswith("uk"):
        return "uk"
    return "en"
