# LockOn Bridge

**Optional Windows helper for [LockOn](https://github.com/blushful8/LockOn)** (War Thunder companion on Android).

## Why this exists

War Thunder’s local API (`:8111`) tells LockOn when you are in a battle and which vehicle you use — but it does **not** give the final **Research Points** and **Silver Lions** after a match. Those numbers only show on the results screen.

**LockOn Bridge** is a small Windows program that detects the end of a battle, briefly reads that screen with **OCR** (Windows packs and optional **Tesseract** for every War Thunder UI language, including Ukrainian), and sends the totals to your phone over Wi‑Fi (port **8112**).

- No Gaijin login  
- Nothing leaves your home network  
- Screenshots are not uploaded  

---

## Do I need it?

| | Without Bridge | With Bridge |
|---|---|---|
| Live vehicle / hangar / battle in LockOn | Yes | Yes |
| Match history | Yes | Yes |
| RP / SL after a match | Manual edit (or stay `0`) | Filled automatically |

If you are fine editing rewards manually in LockOn, you can skip this entirely.

---

## Install

1. Download **`LockOnBridge.zip`** from [Releases](https://github.com/blushful8/LockOnBridge/releases).
2. Extract → run **`LockOnBridge.exe`**.
3. Set **War Thunder language**, turn **Bridge enabled** **ON**.
4. On the phone: **LockOn → Settings → Use LockOn Bridge** (same PC IP; port `8112`).

That is the full path for most players. On first enable, Bridge may ask to install **Windows OCR packs** (Microsoft). For Ukrainian (and a few other cases) it may also offer free offline **Tesseract** via winget — one Yes/No, no manual GitHub hunting.

> Prefer the **ZIP** build. Single-file PyInstaller executables (especially with UPX) are often false-positive’d by Windows Defender. This release is an **onedir** package **without UPX**.

### Windows SmartScreen / Defender

The build is **not code-signed** (no paid certificate). Unsigned PyInstaller apps are often
misclassified by Defender **machine-learning** heuristics — typically:

- `Trojan:Win32/Sabsik.TE.A!ml` (ZIP / onedir exe)
- `Trojan:Win32/Wacatac.B!ml` (older one-file `.exe` builds)

This is a **false positive**, not real malware. Releases are built as **onedir ZIP, no UPX**, from this repo only.

**If Defender deletes the ZIP on download:**

1. Open **Windows Security → Virus & threat protection → Protection history**.
2. Find the LockOn Bridge item → **Actions → Allow / Restore**.
3. Optionally add an exclusion for `%LOCALAPPDATA%\LockOnBridge` (and your Downloads folder while installing).
4. Extract the ZIP, run `LockOnBridge.exe`, turn **Bridge enabled** ON (copies into LocalAppData).

**SmartScreen (“Windows protected your PC”):** More info → **Run anyway**.

**Report to Microsoft** (helps everyone): [Submit a file](https://www.microsoft.com/en-us/wdsi/filesubmission) as a false positive, with the release SHA256 from the release notes. The lasting fix is a paid Authenticode certificate.

### Control window

| | |
|---|---|
| **Enabled ON** | Autostart at Windows logon; wakes with War Thunder; stops when the game exits; tray icon while running |
| **Enabled OFF** | Autostart removed; agent stopped; **no background process** — zero PC load until you turn it on again |
| **Language** | English or Ukrainian |
| **Check for updates** | Asks GitHub Releases; after confirmation downloads and replaces the program, then restarts |
| **Desktop shortcut** | Created on first launch (points to the installed copy under LocalAppData) |
| **Hide to tray** | Window closes to the notification area (only when enabled) |
| **Uninstall** | Removes autostart, firewall rule, and local files |

Logs: `%LOCALAPPDATA%\LockOnBridge\logs\bridge.log`

---

## Uninstall

In LockOn Bridge: **Uninstall…**  
Or: **Windows Settings → Apps → LockOn Bridge → Uninstall**

---

## Tips

- Leave the **results screen** visible for a couple of seconds after the match.  
- Phone and PC on the **same Wi‑Fi**; allow TCP **8112** in Windows Firewall if needed.  
- While the game is open and Bridge is **ACTIVE**, open `http://127.0.0.1:8112/v1/health` — should return `{"ok": true, ...}`.

---

## Privacy

Local LAN only. No cloud. No account. OCR runs only for a few frames right after a battle.

---

## Українською (коротко)

**Навіщо:** гра не віддає Total RP/SL через `:8111`. Bridge на ПК зчитує екран результатів і надсилає цифри в LockOn.

**Встановлення:** ZIP з Releases → `LockOnBridge.exe` → мова WT + **Bridge enabled** → у LockOn увімкніть **Use LockOn Bridge**. OCR-пакети Bridge пропонує сам при першому увімкненні.

**SmartScreen / Defender:** немає платного підпису. Defender часто хибно позначає ZIP як `Sabsik.TE.A!ml` / `Wacatac.B!ml` (ML). Відновіть у **Захист від вірусів → Журнал захисту → Дозволити**, або виключіть `%LOCALAPPDATA%\LockOnBridge`. Беріть лише офіційний ZIP з Releases. Повний фікс — платний code signing.

**Вимкнути без навантаження:** вимкніть **Bridge enabled** — автозапуск знімається, фоновий процес не працює.

Застосунок LockOn: https://github.com/blushful8/LockOn

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
