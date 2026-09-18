# LockOn Bridge

**Optional Windows helper for [LockOn](https://github.com/blushful8/LockOn)** (War Thunder companion on Android).

## Why this exists

War Thunder’s local API (`:8111`) tells LockOn when you are in a battle and which vehicle you use — but it does **not** give the final **Research Points** and **Silver Lions** after a match. Those numbers only show on the results screen.

**LockOn Bridge** runs on your PC, detects the end of a battle, briefly reads that screen with **Windows OCR**, and sends the totals to your phone over Wi‑Fi (port **8112**).

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

If you are fine editing rewards by hand in the app, you can skip this entirely.

---

## Install (once)

1. Install [Python 3 for Windows](https://www.python.org/downloads/) and enable **Add python.exe to PATH**.
2. Download this repo (Code → Download ZIP) or clone it.
3. Open the folder and double-click **`Install LockOn Bridge.bat`**. Wait until it says installed.
4. On the phone: **LockOn → Settings → Use LockOn Bridge** (same PC IP as for the game; port `8112`).

You do **not** need to start it before every session after that.

### What happens after install

- Starts silently when you log into Windows  
- **Idle** while War Thunder is closed (checks for `aces.exe` about every **30 seconds** — no OCR, no HTTP, no `:8111`)  
- **Wakes** when War Thunder starts; **stops completely** when you quit the game  
- Shows up in **Settings → Apps → LockOn Bridge** so you can uninstall it like any other program  

Logs: `%LOCALAPPDATA%\LockOnBridge\logs\bridge.log`

---

## Uninstall

**Windows Settings → Apps → LockOn Bridge → Uninstall**

---

## Tips

- Leave the **results screen** visible for a couple of seconds after the match.  
- Phone and PC on the **same Wi‑Fi**; allow TCP **8112** in Windows Firewall if needed.  
- While the game is open, open `http://127.0.0.1:8112/v1/health` — should return `{"ok": true, ...}`.

---

## Privacy

Local LAN only. No cloud. No account. OCR runs only for a few frames right after a battle.

---

## Українською (коротко)

**Навіщо:** гра не віддає Total RP/SL через `:8111`. Bridge на ПК зчитує екран результатів і надсилає цифри в LockOn.

**Встановлення:** Python 3 → `Install LockOn Bridge.bat` → у телефоні увімкнути **Use LockOn Bridge**.

**Далі:** працює сам із War Thunder; вихід з гри глушить Bridge; видалення — через «Параметри → Програми».

Апка LockOn: https://github.com/blushful8/LockOn

---

## Developers

```bat
py -3 -m pip install -r requirements.txt
py -3 -m lockon_bridge --session
```

`--session` keeps Bridge up until Ctrl+C. Daily use should go through the installer (auto mode linked to War Thunder).
