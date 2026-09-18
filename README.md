# LockOn Bridge

**Optional Windows helper for [LockOn](https://github.com/blushful8/LockOn)** (War Thunder companion on Android).

## Why this exists

War Thunder’s local API (`:8111`) tells LockOn when you are in a battle and which vehicle you use — but it does **not** give the final **Research Points** and **Silver Lions** after a match. Those numbers only show on the results screen.

**LockOn Bridge** is a small Windows program that detects the end of a battle, briefly reads that screen with **Windows OCR**, and sends the totals to your phone over Wi‑Fi (port **8112**).

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

1. Download **`LockOnBridge.exe`** from [Releases](https://github.com/blushful8/LockOnBridge/releases) (or build it — see below).
2. Double‑click the exe → a control window opens.
3. Turn **Bridge enabled** **ON** (optional: change the HTTP port, default `8112`).
4. On the phone: **LockOn → Settings → Use LockOn Bridge** (same PC IP as for the game).

### Windows SmartScreen (“Windows protected your PC”)

The exe is **not code-signed** (no paid certificate). Windows may show a blue/yellow warning the first time. That is expected for open-source builds from GitHub — not a virus alert from Defender scan.

**What to do:**

1. Click **More info**.
2. Click **Run anyway**.

After you run it once, Windows usually stops asking for that same file. Always download from the official [Releases](https://github.com/blushful8/LockOnBridge/releases) page only.

### Control window

| | |
|---|---|
| **Enabled ON** | Autostart at Windows logon; wakes with War Thunder; stops when the game exits; tray icon while running |
| **Enabled OFF** | Autostart removed; agent stopped; **no background process** — zero PC load until you turn it on again |
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

**Встановлення:** завантажте **`LockOnBridge.exe`** з Releases → увімкніть **Bridge enabled** → у LockOn увімкніть **Use LockOn Bridge**.

**SmartScreen:** Windows може показати «ОС Windows захистила цей ПК» (немає платного підпису коду). Натисніть **Додаткові відомості** → **Виконати**. Це очікувано для відкритого програмного забезпечення з GitHub; завантажуйте файл лише з офіційної сторінки Releases.

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

Output: `dist\LockOnBridge.exe`

CLI (no UI): `py -3 -m lockon_bridge --session`
