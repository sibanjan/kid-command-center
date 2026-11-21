"""kcc: the monitor and its helpers.

    kcc run              monitor + the parent's page (http://127.0.0.1:8790), with a watchdog
    kcc startup on|off   start the monitor when this Windows user signs in
    kcc status           is the monitor running
    kcc stop             stop monitoring (asks for the parent password)
    kcc-app              the parent's window (starts the monitor if needed)
"""

from __future__ import annotations

import argparse
import logging
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil

from .store import Store, data_dir

PORT = int(os.environ.get("KCC_PORT", "8790"))
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "KidCommandCenter"
NO_WINDOW = 0x08000000 | 0x00000008  # CREATE_NO_WINDOW | DETACHED_PROCESS


def pythonw() -> str:
    exe = Path(sys.executable)
    w = exe.with_name("pythonw.exe")
    return str(w if w.exists() else exe)


def spawn(*args: str) -> subprocess.Popen:
    return subprocess.Popen([pythonw(), "-m", "kid_command_center", *args],
                            creationflags=NO_WINDOW, close_fds=True)


def port_busy(port: int = PORT) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def run(watchdog: bool = True) -> int:
    import uvicorn

    from .monitor import Monitor
    from .web import create_app
    from .winapi import Desktop

    if port_busy():
        print(f"Already running on http://127.0.0.1:{PORT}")
        return 0
    if sys.stdout is None or sys.stderr is None:
        # Started without a console (pythonw: at sign-in, by the watchdog or the app window).
        # Anything that writes to the console would otherwise crash; send it to the log.
        sys.stdout = sys.stderr = open(data_dir() / "monitor.log", "a", encoding="utf-8", buffering=1)
    logging.basicConfig(filename=data_dir() / "monitor.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("comtypes").setLevel(logging.WARNING)
    store = Store()
    monitor = Monitor(store, Desktop())
    server: uvicorn.Server | None = None

    def stop():
        store.set_meta("clean_stop", "1")  # before anything ends: the watchdog must not restart
        monitor.stop_event.set()
        if server:
            server.should_exit = True

    app = create_app(store, monitor, on_stop=stop)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning",
                                           log_config=None))  # use the log file above
    threading.Thread(target=server.run, name="web", daemon=True).start()

    if watchdog:
        def guard():
            dog = spawn("watchdog", "--pid", str(os.getpid()))
            while not monitor.stop_event.wait(5):
                if dog.poll() is not None:  # the watchdog was ended: bring it back
                    store.event("gap", "The watchdog was ended; it was restarted")
                    dog = spawn("watchdog", "--pid", str(os.getpid()))
            dog.terminate()
        threading.Thread(target=guard, name="watchdog-guard", daemon=True).start()

    monitor.run()
    time.sleep(0.5)  # let the stop response go out
    return 0


def watchdog(pid: int) -> int:
    """Restart the monitor if it is ended by anything but the parent's Stop."""
    if sys.stdout is None or sys.stderr is None:
        sys.stdout = sys.stderr = open(data_dir() / "monitor.log", "a", encoding="utf-8", buffering=1)
    store = Store()
    while True:
        if not psutil.pid_exists(pid):
            if store.get_meta("clean_stop") == "1":
                return 0
            store.event("gap", "The monitor was ended (for example from Task Manager); it was restarted")
            spawn("run")
            return 0
        time.sleep(2)


def stop(password: str | None = None) -> int:
    """Stop the monitor and its watchdog, after checking the parent password (the installer
    uses this for upgrades and uninstalling). Exit code 2: wrong password."""
    import getpass
    import json
    import urllib.request
    from http.cookiejar import CookieJar

    from .auth import check_password

    store = Store()
    stored = store.get_meta("password")
    if stored:
        password = password if password is not None else getpass.getpass("Parent password: ")
        if not check_password(password, stored):
            print("Wrong password.")
            return 2
    store.set_meta("clean_stop", "1")  # the watchdog must not restart it
    if port_busy():
        op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))

        def post(path, body):
            req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
            op.open(req, timeout=10).read()

        try:
            if stored:
                post("/api/login", {"password": password})
            post("/api/monitor/stop", {})
        except Exception as exc:  # fall back to ending the processes below
            print(f"Asking the monitor to stop failed ({exc}); ending it.")
        for _ in range(40):
            if not port_busy():
                break
            time.sleep(0.25)
    # anything left (a stuck monitor, the watchdog, the parent window holding the files open)
    me = psutil.Process()
    mine = {me.pid} | {p.pid for p in me.parents()}
    for p in psutil.process_iter(["pid", "cmdline"]):
        cl = " ".join(p.info["cmdline"] or [])
        if p.info["pid"] not in mine and "kid_command_center" in cl and (
                " run" in cl or " watchdog" in cl or "kid_command_center.app" in cl):
            try:
                p.kill()
            except psutil.Error:
                pass
    print("Monitoring stopped.")
    return 0


def startup(on: bool) -> None:
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ,
                              f'"{pythonw()}" -m kid_command_center run')
        else:
            try:
                winreg.DeleteValue(k, RUN_NAME)
            except FileNotFoundError:
                pass


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="kcc", description="Kid Command Center")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run the monitor and the parent's page")
    r.add_argument("--no-watchdog", action="store_true")
    w = sub.add_parser("watchdog")
    w.add_argument("--pid", type=int, required=True)
    s = sub.add_parser("startup", help="start the monitor at sign-in")
    s.add_argument("state", choices=["on", "off"])
    sub.add_parser("status")
    sub.add_parser("stop", help="stop monitoring (asks for the parent password)")
    args = p.parse_args(argv)
    if args.cmd == "stop":
        return stop()
    if args.cmd == "run":
        return run(not args.no_watchdog)
    if args.cmd == "watchdog":
        return watchdog(args.pid)
    if args.cmd == "startup":
        startup(args.state == "on")
        print(f"Start at sign-in: {args.state}")
        return 0
    if args.cmd == "status":
        print(f"Monitor: {'running' if port_busy() else 'not running'} (http://127.0.0.1:{PORT})")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
