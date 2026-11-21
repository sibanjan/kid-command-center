"""The parent's window: the password-protected page in a desktop window. Starts the monitor
first if it is not running."""

from __future__ import annotations

import time

from .__main__ import PORT, port_busy, spawn


def main() -> int:
    import webview

    if not port_busy():
        spawn("run")
        for _ in range(60):
            if port_busy():
                break
            time.sleep(0.25)
    webview.create_window("Kid Command Center", f"http://127.0.0.1:{PORT}/",
                          width=1180, height=820, min_size=(720, 520))
    webview.start(private_mode=True)  # nothing of the session is kept after the window closes
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
