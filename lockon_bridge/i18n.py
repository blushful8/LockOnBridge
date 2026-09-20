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
    wt_language: str
    ocr_backend: str
    ocr_backend_auto: str
    ocr_backend_windows: str
    ocr_backend_tesseract: str
    setup_tesseract: str
    setup_tesseract_body: str
    setup_tesseract_no_exe: str
    setup_tesseract_done: str
    setup_tesseract_failed: str
    setup_tesseract_offer_title: str
    setup_tesseract_offer_body: str
    setup_tesseract_installing: str
    lang_en: str
    lang_uk: str
    badge_on: str
    badge_off: str
    badge_idle: str
    badge_active: str
    status_disabled: str
    status_enabled_waiting: str
    status_firewall_needed: str
    phone_access_banner: str
    phone_access_ok: str
    phone_test_hint: str
    firewall_warning: str
    firewall_prompt_title: str
    firewall_prompt_body: str
    firewall_menu: str
    firewall_ok: str
    firewall_denied: str
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
    test_ocr: str
    test_ocr_busy: str
    test_ocr_countdown: str
    test_ocr_ok: str
    test_ocr_fail: str
    test_ocr_error: str
    replay_log: str
    replay_ok: str
    replay_fail: str
    ocr_packs: str
    ocr_packs_ok: str
    ocr_packs_missing_title: str
    ocr_packs_missing_body: str
    ocr_packs_uk_note: str
    ocr_packs_install: str
    ocr_packs_skip: str
    ocr_packs_installing: str
    ocr_packs_done: str
    ocr_packs_failed: str
    ocr_packs_nothing: str
    outcome_victory: str
    outcome_defeat: str
    outcome_undecided: str
    menu_more: str
    debug_show_rois: str


EN = Strings(
    subtitle="OCR companion for LockOn (War Thunder)",
    bridge_enabled="Bridge enabled",
    tip=(
        "Choose your War Thunder UI language once. "
        "When you enable Bridge, it can install OCR helpers automatically — "
        "no separate tools for most users."
    ),
    http_port="HTTP port",
    open_logs="Open logs",
    hide_to_tray="Hide to tray",
    check_updates="Check for updates",
    uninstall="Uninstall…",
    quit="Quit",
    language="App language",
    wt_language="War Thunder language",
    ocr_backend="OCR engine",
    ocr_backend_auto="Auto (Windows + Tesseract)",
    ocr_backend_windows="Windows only",
    ocr_backend_tesseract="Tesseract only",
    setup_tesseract="Setup Tesseract OCR…",
    setup_tesseract_body=(
        "Tesseract can read Ukrainian and other WT languages that Windows OCR lacks.\n\n"
        "Status:\n{status}\n\n"
        "Download official tessdata_fast models for {lang} now? (Apache-2.0, from GitHub)"
    ),
    setup_tesseract_no_exe=(
        "Tesseract is not installed.\n\n"
        "Install via winget now?\n"
        "(UB-Mannheim.TesseractOCR — free, offline OCR after install)\n\n"
        "Or later: https://github.com/UB-Mannheim/tesseract/wiki"
    ),
    setup_tesseract_done="Language models ready.\n\n{detail}",
    setup_tesseract_failed="Could not download / install.\n\n{detail}",
    setup_tesseract_offer_title="Better OCR for your WT language?",
    setup_tesseract_offer_body=(
        "Windows OCR alone is weak for {lang} (e.g. no Ukrainian pack).\n\n"
        "Install free offline Tesseract now via winget?\n"
        "One click — recommended. You can skip and rely on Windows packs only."
    ),
    setup_tesseract_installing="Installing Tesseract… Confirm if Windows asks.",
    lang_en="English",
    lang_uk="Ukrainian",
    badge_on="ON",
    badge_off="OFF",
    badge_idle="IDLE",
    badge_active="ACTIVE",
    status_disabled="Disabled — no autostart, no background work",
    status_enabled_waiting="Enabled — waiting for War Thunder",
    status_firewall_needed="Enabled — phone access not allowed yet",
    phone_access_banner=(
        "⚠ PHONE CANNOT REACH THIS PC\n"
        "LockOn History will stay at 0 RP / 0 SL until you fix this.\n\n"
        "Fix: More ▾ → Allow phone access → Yes (then Yes on Windows UAC).\n"
        "If Defender blocked LockOnBridge.exe: Protection history → Allow, then try again.\n"
        "On the phone: same PC IP, Bridge switch ON, port {port}."
    ),
    phone_access_ok="Phone access OK — test from phone browser:\n{url}",
    phone_test_hint="Phone test URL (same Wi‑Fi): {url}",
    firewall_warning=(
        "Phone access was not allowed on this PC.\n\n"
        "Without it, LockOn on the phone stays at 0 RP / 0 SL even when OCR works.\n\n"
        "On THIS computer: More ▾ → Allow phone access → Yes, then Yes on the Windows (UAC) window.\n"
        "Nothing appears on the phone."
    ),
    firewall_prompt_title="Allow phone access? (on this PC)",
    firewall_prompt_body=(
        "So the phone can reach this PC, Windows on THIS computer needs permission once "
        "(firewall, port {port}).\n\n"
        "Next: a Windows security window appears HERE — click Yes.\n"
        "There is no prompt on the phone.\n\n"
        "Allow now?"
    ),
    firewall_menu="Allow phone access…",
    firewall_ok="Phone access allowed on this PC. In LockOn use this PC's IP and port {port}.",
    firewall_denied=(
        "Windows permission was not granted on this PC (UAC cancelled or blocked).\n\n"
        "Try again on THIS computer: More ▾ → Allow phone access → Yes on the Windows window.\n"
        "Nothing will appear on the phone."
    ),
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
    update_dev_only="Automatic updates work with the packaged LockOn Bridge build. Download the ZIP release to update.",
    update_restarting="Update downloaded. LockOn Bridge will restart.",
    test_ocr="Test OCR now",
    test_ocr_busy="Reading screen…",
    test_ocr_countdown="Switch to WT results — capture in {n}…",
    test_ocr_ok=(
        "Parsed rewards:\n"
        "Research Points: {rp}\n"
        "Silver Lions: {sl}\n"
        "Outcome: {outcome}\n"
        "Confidence: {conf:.0%}"
    ),
    test_ocr_fail=(
        "No RP/SL found on screen.\n\n"
        "You need the post-battle RESULTS screen (with / without premium totals),\n"
        "not the hangar or main menu.\n\n"
        "1) Open that screen in War Thunder (or a full-screen screenshot of it)\n"
        "2) Click «Test OCR» — Bridge hides and waits 5 seconds\n"
        "3) Stay on the results screen until the dialog appears\n\n"
        "OCR preview:\n{preview}"
    ),
    test_ocr_error="OCR failed:\n{error}",
    replay_log="Replay last OCR dump",
    replay_ok="Replayed last OCR dump:\nResearch Points: {rp}\nSilver Lions: {sl}",
    replay_fail="Could not parse last OCR dump (or file missing).",
    ocr_packs="Windows OCR packs…",
    ocr_packs_ok="Recommended Windows OCR packs are already installed:\n{packs}",
    ocr_packs_missing_title="Install Windows OCR packs?",
    ocr_packs_missing_body=(
        "Recommended Windows OCR packs for ({lang}):\n{packs}\n\n"
        "Missing:\n{missing}\n\n"
        "{note}"
        "Install from Microsoft Windows Update now?"
    ),
    ocr_packs_uk_note=(
        "Note: Windows has no Ukrainian OCR pack. "
        "Use Tesseract (ukr) or Russian Windows OCR for Cyrillic.\n\n"
    ),
    ocr_packs_install="Install from Microsoft",
    ocr_packs_skip="Not now",
    ocr_packs_installing="Installing OCR packs… Confirm UAC if asked.",
    ocr_packs_done="OCR packs installed.\n\n{detail}",
    ocr_packs_failed="Could not install OCR packs.\n\n{detail}",
    ocr_packs_nothing="Nothing to install — required packs are present.",
    outcome_victory="victory",
    outcome_defeat="defeat",
    outcome_undecided="undecided",
    menu_more="More ▾",
    debug_show_rois="Show OCR regions (dev)",
)

UK = Strings(
    subtitle="OCR-супутник для LockOn (War Thunder)",
    bridge_enabled="Bridge увімкнено",
    tip=(
        "Один раз оберіть мову інтерфейсу War Thunder. "
        "Після увімкнення Bridge сам запропонує OCR-пакети — "
        "окремі утиліти більшості користувачів не потрібні."
    ),
    http_port="Порт HTTP",
    open_logs="Відкрити журнали",
    hide_to_tray="Згорнути в трей",
    check_updates="Перевірити оновлення",
    uninstall="Видалити…",
    quit="Вийти",
    language="Мова програми",
    wt_language="Мова War Thunder",
    ocr_backend="Рушій OCR",
    ocr_backend_auto="Авто (Windows + Tesseract)",
    ocr_backend_windows="Лише Windows",
    ocr_backend_tesseract="Лише Tesseract",
    setup_tesseract="Налаштувати Tesseract OCR…",
    setup_tesseract_body=(
        "Tesseract читає українську та інші мови WT, яких немає у Windows OCR.\n\n"
        "Статус:\n{status}\n\n"
        "Завантажити офіційні моделі tessdata_fast для {lang}? (Apache-2.0, GitHub)"
    ),
    setup_tesseract_no_exe=(
        "Tesseract не встановлено.\n\n"
        "Встановити зараз через winget?\n"
        "(UB-Mannheim.TesseractOCR — безкоштовно, далі офлайн)\n\n"
        "Або пізніше: https://github.com/UB-Mannheim/tesseract/wiki"
    ),
    setup_tesseract_done="Мовні моделі готові.\n\n{detail}",
    setup_tesseract_failed="Не вдалося завантажити / встановити.\n\n{detail}",
    setup_tesseract_offer_title="Кращий OCR для вашої мови WT?",
    setup_tesseract_offer_body=(
        "Лише Windows OCR слабкий для {lang} (наприклад, немає українського пакета).\n\n"
        "Встановити безкоштовний офлайн Tesseract через winget?\n"
        "Один клік — рекомендовано. Можна пропустити й лишити лише пакети Windows."
    ),
    setup_tesseract_installing="Встановлення Tesseract… Підтвердіть запит Windows, якщо з’явиться.",
    lang_en="English",
    lang_uk="Українська",
    badge_on="УВІМК.",
    badge_off="ВИМК.",
    badge_idle="ОЧІКУВАННЯ",
    badge_active="АКТИВНИЙ",
    status_disabled="Вимкнено — немає автозапуску й фонової роботи",
    status_enabled_waiting="Увімкнено — очікування War Thunder",
    status_firewall_needed="Увімкнено — доступ з телефона ще не дозволено",
    phone_access_banner=(
        "⚠ ТЕЛЕФОН НЕ ДОХОДИТЬ ДО ЦЬОГО ПК\n"
        "В Історії LockOn лишатимуться 0 RP / 0 SL, доки це не виправите.\n\n"
        "Виправлення: More ▾ → Дозволити доступ з телефона → Так (потім Так у UAC Windows).\n"
        "Якщо Defender заблокував LockOnBridge.exe: Журнал захисту → Дозволити, і знову.\n"
        "На телефоні: той самий IP ПК, перемикач Bridge УВІМК., порт {port}."
    ),
    phone_access_ok="Доступ з телефона OK — перевірка з браузера телефона:\n{url}",
    phone_test_hint="URL для перевірки з телефона (та сама Wi‑Fi): {url}",
    firewall_warning=(
        "Доступ з телефона не дозволено на цьому ПК.\n\n"
        "Без цього LockOn на телефоні лишатиме 0 RP / 0 SL, навіть якщо OCR працює.\n\n"
        "На ЦЬОМУ комп’ютері: More ▾ → Дозволити доступ з телефона → «Так», "
        "потім «Так» у вікні Windows (UAC).\n"
        "На телефоні запиту не буде."
    ),
    firewall_prompt_title="Дозволити доступ з телефона? (на цьому ПК)",
    firewall_prompt_body=(
        "Щоб телефон дістався цього ПК, Windows на ЦЬОМУ комп’ютері один раз "
        "потребує дозволу (брандмауер, порт {port}).\n\n"
        "Далі з’явиться вікно безпеки Windows ТУТ — натисніть «Так».\n"
        "На телефоні нічого не з’явиться.\n\n"
        "Дозволити зараз?"
    ),
    firewall_menu="Дозволити доступ з телефона…",
    firewall_ok="Доступ з телефона дозволено на цьому ПК. У LockOn — IP цього ПК і порт {port}.",
    firewall_denied=(
        "Дозвіл Windows на цьому ПК не надано (скасовано UAC або заблоковано).\n\n"
        "Спробуйте знову на ЦЬОМУ комп’ютері: More ▾ → Дозволити доступ з телефона "
        "→ «Так» у вікні Windows.\n"
        "На телефоні запиту не буде."
    ),
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
    update_dev_only="Автоматичні оновлення працюють із зібраною програмою LockOn Bridge. Завантажте ZIP-реліз для оновлення.",
    update_restarting="Оновлення завантажено. LockOn Bridge перезапуститься.",
    test_ocr="Перевірити OCR",
    test_ocr_busy="Читаю екран…",
    test_ocr_countdown="Перемкнись на результати WT — знімок через {n}…",
    test_ocr_ok=(
        "Розпізнано нагороди:\n"
        "Очки досліджень: {rp}\n"
        "Срібні леви: {sl}\n"
        "Результат: {outcome}\n"
        "Впевненість: {conf:.0%}"
    ),
    test_ocr_fail=(
        "RP/SL на екрані не знайдено.\n\n"
        "Потрібен екран РЕЗУЛЬТАТІВ після бою (з / без преміуму),\n"
        "а не ангар чи головне меню.\n\n"
        "1) Відкрий цей екран у War Thunder (або повноекранний скрін)\n"
        "2) Натисни «Перевірити OCR» — Bridge сховається на 5 с\n"
        "3) Залишайся на екрані результатів до появи вікна\n\n"
        "Фрагмент OCR:\n{preview}"
    ),
    test_ocr_error="Помилка OCR:\n{error}",
    replay_log="Повторити останній OCR",
    replay_ok="Останній OCR-дамп:\nОчки досліджень: {rp}\nСрібні леви: {sl}",
    replay_fail="Не вдалося розпарсити останній OCR-дамп (або файлу немає).",
    ocr_packs="Пакети Windows OCR…",
    ocr_packs_ok="Рекомендовані пакети Windows OCR уже встановлені:\n{packs}",
    ocr_packs_missing_title="Встановити пакети Windows OCR?",
    ocr_packs_missing_body=(
        "Рекомендовані пакети Windows OCR для ({lang}):\n{packs}\n\n"
        "Бракує:\n{missing}\n\n"
        "{note}"
        "Встановити з Microsoft Windows Update?"
    ),
    ocr_packs_uk_note=(
        "У Windows немає українського OCR. "
        "Для кирилиці — Tesseract (ukr) або російський Windows OCR.\n\n"
    ),
    ocr_packs_install="Встановити від Microsoft",
    ocr_packs_skip="Не зараз",
    ocr_packs_installing="Встановлення пакетів OCR… Підтвердіть UAC.",
    ocr_packs_done="Пакети OCR встановлено.\n\n{detail}",
    ocr_packs_failed="Не вдалося встановити пакети OCR.\n\n{detail}",
    ocr_packs_nothing="Нічого встановлювати — потрібні пакети вже є.",
    outcome_victory="перемога",
    outcome_defeat="поразка",
    outcome_undecided="невідомо",
    menu_more="Ще ▾",
    debug_show_rois="Показати OCR-області (dev)",
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
