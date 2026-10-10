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

## Never commit

`.env`, `*.session` and `logs/` are in `.gitignore`. They hold the MT5 password and the full Telegram login.
