# Build & deploy

## CI (GitHub Actions)

`.github/workflows/ci.yml` runs only:

- when a pull request is **merged** into `main` (opening or updating a PR does not run it; a PR closed without merging is skipped),
- when a `v*` tag is pushed,
- by hand: Actions tab → CI → **Run workflow**. Use this to test a branch before merging.

Each run:

1. Runs the tests (`pytest`).
2. Builds `TrackingGold.exe` (`build.ps1 -Version <v> -Package`).
3. Uploads `TrackingGold-<version>.zip` as a workflow artifact (kept 14 days).
4. On a `v*` tag only: publishes the zip as a GitHub Release.

## Releasing a new version

```bash
git tag v1.1.0
git push origin v1.1.0
```

## Updating the VPS

The VPS pulls releases itself, so it needs no open ports and GitHub holds no VPS credentials.
`update.ps1` only replaces the exe and helper scripts. It never touches `.env`, `tracking_session.session` or `logs\`.

| Command (run in `C:\TrackingGold`) | What it does |
|---|---|
| `powershell -ExecutionPolicy Bypass -File update.ps1` | Installs the latest release if it is newer. Refuses if a signal arrived in the last 15 minutes. |
| `... update.ps1 -Force` | Same, without the recent-signal check |
| `... update.ps1 -Rollback` | Restores the previous exe |
| `... update.ps1 -InstallSchedule` | Auto-updates every Saturday 10:00, while the gold market is closed |

After restarting the bot, the updater waits for `Listening to channel` in the log.
If that line doesn't appear within about 2 minutes, it rolls back to the previous exe automatically.

If the repo is made private, create a fine-grained GitHub token with read-only **Contents** access to this repo.
Then save it on the VPS:

```bash
[Environment]::SetEnvironmentVariable("GITHUB_TOKEN", "<token>", "User")
```

## Running several bots on one VPS

Each bot is a separate folder (`C:\TrackingGold`, `C:\TrackingGold2`, …) with its own `TrackingGold.exe`, `.env`, Telegram session and `logs\`.
The scheduled tasks are named after the folder: `TrackingGold`, `TrackingGold2`, `TrackingGold2-Update`.
`update.ps1` in one folder only stops and restarts the bot of that folder.

For each extra bot:

1. **Install a second MT5 terminal in its own folder.** In the MT5 installer, open *Settings* and pick another installation folder, e.g. `C:\Program Files\MetaTrader 5 - Bot2`.
   Log it in to the second account and turn on Algo Trading.
2. Copy the release files (`TrackingGold.exe`, `.env.example`, the `.ps1` scripts) into the new folder.
   **Don't copy `tracking_session.session`**: two bots must not share one Telegram session.
3. Create the new folder's `.env`:
   - `MT5_LOGIN` / `MT5_PASSWORD` / `MT5_SERVER` for the second account.
   - `SIGNAL_CHANNEL` for the second channel.
   - **`MT5_PATH` set to the second terminal's `terminal64.exe`.** The first bot's `.env` also needs `MT5_PATH` pointing at its own terminal.
     Without it, both bots attach to the same terminal and keep logging it in to each other's account.
4. Run `TrackingGold.exe` from the new folder by hand once.
   It asks for the Telegram phone number and code, then creates its own session.
   The same Telegram account works: each bot is just another logged-in device.
5. In the new folder, run `install_autostart.ps1`, then `update.ps1 -InstallSchedule`.

Two bots can only run side by side if they trade different MT5 accounts or use different `SIGNAL_MAGIC` values.

## Never commit

`.env`, `*.session` and `logs/` are in `.gitignore`. They hold the MT5 password and the full Telegram login.
