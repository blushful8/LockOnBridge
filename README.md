# LockOn Bridge

LockOn Bridge is a Windows companion for War Thunder. It reads Research Points (RP) and Silver Lions (SL) from the post-battle results screen and makes the recognized rewards available to the LockOn phone app over your local network.

No Gaijin account or game login is required.

## Install and connect

1. Download **LockOnBridge.zip** from the [latest release](https://github.com/blushful8/LockOnBridge/releases/latest).
2. Extract the entire folder and run **LockOnBridge.exe**. Keep the accompanying files next to the executable.
3. Choose the app language and OCR backend, then turn **Bridge ON**.
4. Allow phone access when prompted. On the phone, enable the Bridge connection and enter this PC's local IP address and port **8112**.
5. Keep the phone and PC on the same local network.

The Windows build is unsigned and distributed as a folder-based ZIP without UPX. Check that the download comes from this repository; the release notes include the archive's SHA256 checksum.

## Reading battle rewards

While enabled, Bridge monitors War Thunder and captures a short sequence of game-window screenshots after a battle. Keep the results screen visible and in the foreground until capture finishes. Input is temporarily paused during capture, then released while recognition continues. A progress notice remains visible during processing.

Bridge reads all six captured screenshots before choosing a result. When the recognized RP and SL stay equal or increase, it uses the latest successfully read screenshot, even if the last two differ. If either value decreases, it uses the last matching neighboring pair before the first decrease. If there is no earlier matching pair, it falls back to the latest successful read. OCR can misread animated, obscured or incomplete results; a report is not guaranteed for every battle.

The phone's premium-account preference determines whether Bridge reads the with-premium or without-premium rewards. A battle session ID connects the report to its battle and remains available until a new battle starts.

## OCR backends

- **OCR.space** is the default cloud backend. Only a crop of the results screen is sent to the service. It requires internet access and is subject to the service's availability and usage limits.
- **EasyOCR** performs recognition on the PC. On first selection, Bridge downloads a private Python runtime, CPU dependencies and OCR models. The current backend remains active during preparation; EasyOCR is selected after setup succeeds. Errors appear in the window, and selecting EasyOCR again retries preparation.

Prepare EasyOCR before starting a battle if you want local recognition. If OCR.space is unavailable or its usage limit is reached, Bridge can attempt local fallback, but a report may be missed while local setup is incomplete.

The app interface supports **English, Ukrainian and Russian**. EasyOCR currently loads **Ukrainian and English** models; changing the War Thunder language selector does not change that model set. OCR.space uses automatic language detection.

## Everyday controls

| Control | Behavior |
|---|---|
| **Bridge ON** | Enables capture, local HTTP access and Windows logon startup. |
| **Bridge OFF** | Stops the background agent and removes logon startup. The control window can remain open. |
| **OCR** | Selects OCR.space or prepares and selects EasyOCR. |
| **HTTP port** | Sets the local connection port; the default is 8112. |
| **Check for updates** | Checks GitHub Releases and, after confirmation, downloads the update and restarts the app. |
| **Hide to tray** | Keeps an enabled Bridge running in the notification area. |
| **Allow phone access** | Helps configure the Windows firewall for the selected port. |
| **Uninstall** | Starts the included uninstaller. |

When War Thunder closes, capture monitoring stops. While Bridge remains enabled, its HTTP server stays available so the phone can retrieve the last report.

## Local connection

The HTTP API uses port **8112** by default:

| Endpoint | Response |
|---|---|
| `GET /v1/health` | Service status, app version and premium-account preference. |
| `GET /v1/session` | Current battle session ID and its active state. |
| `GET /v1/latest-report` | Latest saved report, or HTTP 204 when none exists. |
| `GET /v1/reports` | Saved reports inside a `reports` array. |
| `GET /v1/preferences` | Current `hasPremiumAccount` preference. |
| `PUT /v1/preferences` | Updates `hasPremiumAccount` with a JSON body. |

Reports include RP, SL, capture time, OCR confidence, outcome and session ID. Older saved reports may have an empty session ID. This is a local HTTP connection without account authentication; use it on a trusted network.

## Data and privacy

Reports, settings and diagnostic logs are stored on the PC. The latest capture sequence can retain up to six game-window screenshots locally. Captures can contain information visible in the game window.

With OCR.space selected, the reward crop is sent to that external service. With EasyOCR selected and ready, recognition runs locally; initial setup still downloads software and models. A saved OCR.space key is encrypted for the current Windows account.

The release ZIP contains application resources, not personal captures, logs, reports or saved keys. Real screenshot fixtures are excluded from the current source tree. Older commits and release source archives can still contain historical test screenshots.

## Troubleshooting and uninstall

- Keep the results screen open until capture finishes, especially while reward numbers are still counting.
- Check that the phone uses the correct PC IP and port on the same network.
- Use **Allow phone access** if Windows firewall prevents the connection.
- Open `http://127.0.0.1:8112/v1/health` on the PC to check an enabled Bridge.
- Logs are normally under `%LOCALAPPDATA%\LockOnBridge\logs\bridge.log`.

Uninstall from the app, Windows Installed apps, or **uninstall.exe** next to the installed executable. Separately downloaded EasyOCR files may remain: on PCs with a D: drive, they are stored under `D:\CodexData\LockOnBridge\ocr`; otherwise they are stored under the app's local data directory.
