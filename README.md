# LockOn Bridge

Windows program for War Thunder. After a battle it reads the results screen and publishes one **Research Points** and **Silver Lions** pair on this PC. A phone on the same Wi‑Fi reads that pair over HTTP.

No Gaijin login. The game’s local API does not include the final RP and SL; those numbers only appear on the results screen.

---

## What it does

- With **Bridge** on, it starts at Windows logon, waits for War Thunder, and stops when the game exits.
- After a battle, while the results screen stays open, it takes several shots of the reward strip.
- It publishes one RP/SL pair only when two reads agree. The phone chooses the column: with premium or without.

Local HTTP, default port **8112**:

| | |
|---|---|
| `GET /v1/health` | Bridge is up (`ok`, version, premium flag) |
| `GET /v1/session` | `{ "sessionId": "<uuid>", "active": true }` for the battle in progress. The id appears when the hangar opens a battle and stays through the results screen and the published report. It changes only when the next battle starts. After the battle, `active` may be `false` while `sessionId` stays. |
| `GET /v1/latest-report` | Last published pair, or `204` when there is none |
| `GET /v1/reports` | Stored reports |
| `GET` / `PUT /v1/preferences` | `{ "hasPremiumAccount": true }` or `false` |

`latest-report` and each item in `reports` include `sessionId` for that battle, plus `researchPoints`, `silverLions`, `capturedAtEpochMillis`, `confidence`, and `outcome`. `sessionId` is empty only on reports saved by an older build.

---

## OCR

- **OCR.space** (Engine 3) is the default. Only the reward crop is sent to `api.ocr.space`.
- Your key is read from the `OCR_SPACE_API_KEY` environment variable, or from an encrypted file on this Windows account. It is never stored in this repository. Without a key, the public demo key is used and runs out quickly. Free Engine 3 is **2,500** requests a month and **500** a day.
- **EasyOCR** runs on the PC. Choose it in the window, or leave OCR.space selected: Bridge installs EasyOCR when the API is unreachable or the monthly or daily limit is spent. The first install needs **Python 3.12** on the PC and takes a few minutes. The battle that triggered the install is not held open for that download.
- App language: **English**, **Ukrainian**, or **Russian**. Set **War Thunder language** to the language of the results screen.

---

## Install

1. Download **`LockOnBridge.zip`** from [Releases](https://github.com/blushful8/LockOnBridge/releases).
2. Extract and run **`LockOnBridge.exe`**.
3. Set the app language, the War Thunder language, and OCR (**OCR.space** by default). Turn **Bridge** **ON**.
4. When asked **Allow phone access?** choose **Yes**, then **Yes** on the Windows prompt (once).

If you skipped that prompt: **More ▾ → Allow phone access…**

On the phone, use this PC’s IP and port `8112`, on the same Wi‑Fi.

Prefer the **ZIP**. This release is an **onedir** package **without UPX**. Single-file PyInstaller executables are often false-positive’d by Windows Defender.

### Windows SmartScreen / Defender

The build is **not code-signed**. Unsigned PyInstaller apps are often misclassified by Defender, typically:

- `Trojan:Win32/Sabsik.TE.A!ml` (ZIP / onedir exe)
- `Trojan:Win32/Wacatac.B!ml` (older one-file `.exe` builds)

This is a **false positive**. Releases are built from this repo only.

**If Defender deletes the ZIP on download:**

1. Open **Windows Security → Virus & threat protection → Protection history**.
2. Find the LockOn Bridge item → **Actions → Allow / Restore**.
3. Optionally add an exclusion for `%LOCALAPPDATA%\LockOnBridge`.
4. Extract the ZIP, run `LockOnBridge.exe`, and turn **Bridge** ON (copies into LocalAppData).

**SmartScreen (“Windows protected your PC”):** More info → **Run anyway**.

**Report to Microsoft:** [Submit a file](https://www.microsoft.com/en-us/wdsi/filesubmission) as a false positive, with the release SHA256 from the release notes.

### Control window

| | |
|---|---|
| **Bridge ON** | Autostart at Windows logon; wakes with War Thunder; stops when the game exits; tray icon while running |
| **Bridge OFF** | Autostart removed; agent stopped; no background process until you turn it on again |
| **App language** | English, Ukrainian, or Russian |
| **War Thunder language** | Language of the results screen |
| **OCR** | OCR.space or EasyOCR |
| **HTTP port** | Default `8112` |
| **Check for updates** | GitHub Releases; after confirmation, downloads, replaces the program, and restarts |
| **Desktop shortcut** | Created on first launch (installed copy under LocalAppData) |
| **Hide to tray** | Window closes to the notification area (only when enabled) |
| **Uninstall** | In-app **Uninstall…**, or **`uninstall.exe`** next to `LockOnBridge.exe` |

Logs: `%LOCALAPPDATA%\LockOnBridge\logs\bridge.log`

---

## Uninstall

Any of:

- Double-click **`uninstall.exe`** next to `LockOnBridge.exe` (ZIP extract or `%LOCALAPPDATA%\LockOnBridge\app`)
- In the window: **Uninstall…**
- **Windows Settings → Apps → LockOn Bridge → Uninstall**

This stops Bridge, removes autostart, the firewall rule for port 8112, the Desktop shortcut, the Apps & Features entry, and deletes `%LOCALAPPDATA%\LockOnBridge` (app, logs, settings, the stored OCR key).

---

## Tips

- Leave the **results screen** visible until the RP and SL numbers finish counting. Bridge takes several shots over a few seconds and sends a pair only when two of them match.
- Phone and PC on the **same Wi‑Fi**. Allow TCP **8112** if you skipped the prompt.
- While Bridge is **ACTIVE**, open `http://127.0.0.1:8112/v1/health` on this PC. It should return `{"ok": true, ...}`.

---

## Privacy

The published pair stays on your LAN. Bridge does not sign in to Gaijin and does not upload the whole screen.

With **OCR.space**, the reward crop is sent to `api.ocr.space`. With **EasyOCR**, that read stays on the PC. The OCR.space key, if you saved one, stays encrypted in your Windows profile.

---

## Українською

**Навіщо:** після бою гра показує RP і SL лише на екрані результатів. Bridge зчитує цю смугу і віддає одну пару на цей ПК. Телефон у тій самій Wi‑Fi читає її по HTTP, порт **8112**. `GET /v1/session` віддає номер бою: він з’являється на вході в бій і лишається тим самим у звіті, аж доки не почнеться наступний.

**OCR:** за замовчуванням **OCR.space** (Engine 3). На сервіс іде лише вирізка нагороди. Свій ключ — змінна `OCR_SPACE_API_KEY` або зашифрований файл у профілі Windows, не в репозиторії. Без ключа працює публічний демо-ключ, і він швидко закінчується (безкоштовно 2500 запитів на місяць і 500 на день). **EasyOCR** стоїть на ПК: його можна вибрати, і він сам ставиться, якщо API недоступне або ліміт вичерпано. Перше встановлення потребує Python 3.12.

**Встановлення:** ZIP з Releases → `LockOnBridge.exe` → мова програми, мова War Thunder, OCR → **Bridge** увімкнено → **Дозволити доступ з телефона**. На телефоні: IP цього ПК і порт `8112`.

**SmartScreen / Defender:** немає платного підпису. Defender часто хибно позначає ZIP як `Sabsik.TE.A!ml` / `Wacatac.B!ml`. Відновіть у **Захист від вірусів → Журнал захисту → Дозволити**. Беріть лише ZIP з Releases.

**Вимкнути:** вимкніть **Bridge** — автозапуск знімається, фоновий процес не працює.

**Видалити:** **`uninstall.exe`** поруч із `LockOnBridge.exe`, або **Видалити…** у вікні / Параметри Windows → Застосунки.

---

## Developers

```bat
py -3 -m pip install -r requirements.txt
py -3 -m lockon_bridge --ui
```

Build the exe:

```bat
Build LockOn Bridge.bat
```

Output: `dist\LockOnBridge\` and `dist\LockOnBridge.zip`

CLI (no UI): `py -3 -m lockon_bridge --session`
