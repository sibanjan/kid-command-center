# Kid Command Center

A parent's view of what is opened on a child's Windows laptop, with a lock (kill switch) and
daily time limits for any app or site. It runs on the laptop itself; nothing is sent anywhere,
and everything is behind a parent password.

It does not use Windows parental controls, browser extensions or browser policies. It reads
the window in front and the browser's address bar (UI Automation, the way a screen reader
does), ends locked programs and closes locked tabs.

## What you see

- **Today**: one row per app or site, with how many times it was opened, time in front, first
  and last use, and **Lock** / **Limit** buttons.
- **Opened**: a clean list, newest first, of every app started and every site opened
  (time, what, which browser, page).
- **Locks & limits**: everything you set, with time used today, **Unlock**, **+15 / +30 min**
  for today, and **Remove**.
- **Activity log**: when a lock or limit stepped in, warnings shown, and any time monitoring
  was interrupted.

Pick an earlier day at the top to see its history.

## Locks and limits

| Rule | Example | Effect |
|---|---|---|
| Lock an app | `roblox.exe` | Ended at once and whenever it starts, until you unlock it |
| Lock a site | `youtube.com` | Its tab is closed whenever it comes to the front, in any browser |
| Limit a site in one browser | YouTube in Chrome, 30 min/day | Time in front counts; at 5 and 1 min left he gets a warning; then it is closed until tomorrow |
| Limit an app | `minecraft.windows.exe`, 60 min/day | Same, for a program |

A site rule covers its subdomains (`youtube.com` includes `m.youtube.com` and
`music.youtube.com`). Limits reset at midnight. When something is closed he sees a message
saying why and to ask a parent.

## Install (on his laptop, signed in as him)

Sign in to Windows **as him**, open PowerShell and paste:

```powershell
irm https://github.com/sibanjan/kid-command-center-releases/releases/latest/download/install.ps1 | iex
```

The release files are in the public repo
[kid-command-center-releases](https://github.com/sibanjan/kid-command-center-releases); this
code repo stays private. Without internet access to GitHub, install from files instead: open
PowerShell in an extracted release zip and run `iex (Get-Content .\install.ps1 -Raw)`, or in a
copy of this folder and run `iex (Get-Content .\installer\install.ps1 -Raw)`.

**Release a new version:** bump `version` in `pyproject.toml`, run
`powershell -ExecutionPolicy Bypass -File installer\build.ps1`, then publish
`dist\KidCommandCenter-<version>\*` with
`gh release create v<version> --repo sibanjan/kid-command-center-releases --latest <files>`.

No admin rights are needed, and it works with Windows Smart App Control on (it runs no
unsigned program). It downloads Python from python.org's NuGet package and the libraries from
PyPI (about 60 MB), installs into `%LOCALAPPDATA%\Programs\KidCommandCenter`, starts
monitoring at sign-in, and opens the parent window: **set the parent password there**.

Afterwards, open **Kid Command Center** from the Start menu. The page is also at
http://127.0.0.1:8790 on that laptop, behind the same password.

**Upgrade:** run the new release's `install.ps1` the same way; it asks for the parent password.
**Remove:** `iex (Get-Content "$env:LOCALAPPDATA\Programs\KidCommandCenter\uninstall.ps1" -Raw)`;
it asks for the parent password too. **Stop monitoring for a while:** Settings > Stop monitoring,
or `kcc stop`.

## Limits of what it can enforce

- **Admin rights.** A Windows administrator can end any program. If he can open Task Manager
  and end both the monitor and its watchdog, monitoring stops; the next start logs the gap in
  the Activity log ("not running for 25 min"). Ending just one of them restarts it within
  seconds.
- **Per Windows account.** It records the account it runs in. If you share his account, your
  use is recorded too.
- **Background tabs.** Time counts for the tab in front. A video playing in a tab behind
  another one is not counted, but it is closed as soon as it comes to the front once its time
  is up.
- **Browsers.** Chrome, Edge, Brave, Opera and Vivaldi report the page address reliably;
  Firefox is best effort. Any other browser is still recorded as an app and can be locked.

## Data

`%LOCALAPPDATA%\KidCommandCenter\activity.db` (SQLite) holds activity, rules and the
password hash (salted PBKDF2, never the password). `monitor.log` is next to it. Set
`KCC_HOME` to use another folder and `KCC_PORT` for another port.

## Development

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\pythonw.exe -m kid_command_center.app   # the parent window (starts the monitor)
```

Start it with `python -m` / `pythonw -m`: Smart App Control blocks the `kcc.exe` and
`kcc-app.exe` launchers pip creates.
