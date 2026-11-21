"""The monitor: once a second, record what is in front and apply the parent's rules.

Locked apps are ended wherever they run (in front or not, checked every two seconds). A locked
site is closed (its tab) when it comes to the front, and the child sees why. Limits count time
in front only, and not while the screen is locked.
"""

from __future__ import annotations

import logging
import threading
import time

from . import rules as R
from .activity import Tracker, friendly
from .store import Store, day_of

log = logging.getLogger(__name__)

HEARTBEAT = 10  # seconds; a longer silence is logged as a gap when the monitor comes back


class Monitor:
    def __init__(self, store: Store, desktop, tick: float = 1.0):
        self.store, self.desktop, self.tick = store, desktop, tick
        self.tracker = Tracker(store)
        self.stop_event = threading.Event()
        self._rules: list[dict] = []
        self._rules_at = 0.0
        self._last = None
        self._last_scan = 0.0
        self._last_beat = 0.0

    # ---- lifecycle --------------------------------------------------------------
    def start_note(self, now: float | None = None) -> None:
        now = now or time.time()
        beat = self.store.get_meta("heartbeat")
        clean = self.store.get_meta("clean_stop") == "1"
        if beat and not clean and now - float(beat) > 3 * HEARTBEAT:
            self.store.event("gap", f"The monitor was not running for {_mins(now - float(beat))} "
                                    f"(it was ended, or the laptop was off)", ts=float(beat))
        self.store.set_meta("clean_stop", "0")
        self.store.event("monitor_start", "Monitoring started", ts=now)

    def run(self) -> None:
        self.start_note()
        while not self.stop_event.is_set():
            t = time.time()
            try:
                self.step()
            except Exception:  # never let one odd window stop the monitor
                log.exception("monitor step failed")
            self.stop_event.wait(max(0.05, self.tick - (time.time() - t)))
        self.store.set_meta("clean_stop", "1")
        self.store.event("monitor_stop", "Monitoring stopped by the parent")

    def reload_rules(self) -> None:
        self._rules_at = 0.0

    # ---- one tick -----------------------------------------------------------------
    def rules(self, now: float) -> list[dict]:
        if now - self._rules_at > 5:
            self._rules = [r for r in self.store.rules() if r["enabled"]]
            self._rules_at = now
        return self._rules

    def step(self, sample=None) -> None:
        s = sample or self.desktop.sample()
        now = s.ts
        dt = 0.0 if self._last is None else min(now - self._last, 3 * self.tick)
        self._last = now
        day = day_of(now)
        item = self.tracker.update(s)
        rules = self.rules(now)
        usage = self.store.usage(day) if rules else {}

        if item:
            kind, key = item
            for rule in rules:
                if not R.matches(rule, kind, key, s.exe):
                    continue
                u = usage.get(rule["id"])
                if rule["mode"] == "limit" and dt:
                    self.store.add_usage(day, rule["id"], dt)
                    u = {**(u or {}), "seconds": (u or {}).get("seconds", 0) + dt}
                st = R.status(rule, u)
                if st["locked"]:
                    self.enforce(rule, kind, key, s)
                    break
                warn = R.due_warning(rule, u)
                if warn:
                    minutes, flag = warn
                    self.store.mark_warned(day, rule["id"], flag)
                    self.store.event("warning", f"{friendly(kind, key)}: {minutes} min left today", ts=now)
                    self.desktop.notify(f"warn{rule['id']}", f"{friendly(kind, key)}: {minutes} "
                                        f"minute{'s' if minutes > 1 else ''} left for today.", every=0)

        if now - self._last_scan >= 2:
            self._last_scan = now
            self.scan_apps(rules, usage)
            self.tracker.forget_dead(self.desktop.pids())
        if now - self._last_beat >= HEARTBEAT:
            self._last_beat = now
            self.store.set_meta("heartbeat", str(now))

    def enforce(self, rule: dict, kind: str, key: str, s) -> None:
        name = friendly(kind, key)
        why = ("locked by a parent" if rule["mode"] == "block"
               else f"over today's {rule['minutes']} minutes")
        if kind == "app":
            n = self.desktop.kill(rule["target"])
            detail = f"{name} was closed: {why}"
        else:
            self.desktop.close_tab(s.hwnd)
            detail = f"{name} was closed in {friendly('app', s.exe)}: {why}"
            n = 1
        if n:
            self.store.event("blocked" if rule["mode"] == "block" else "limit_reached", detail, ts=s.ts)
        msg = (f"{name} is locked. Ask a parent to unlock it." if rule["mode"] == "block"
               else f"Time is up for {name} today ({rule['minutes']} minutes). Ask a parent for more time.")
        self.desktop.notify(f"lock{rule['id']}", msg)

    def scan_apps(self, rules: list[dict], usage: dict) -> None:
        """End locked apps even when they run in the background."""
        locked = [r for r in rules if r["kind"] == "app" and R.status(r, usage.get(r["id"]))["locked"]]
        if not locked:
            return
        running = self.desktop.running()
        for r in locked:
            if r["target"] in running and self.desktop.kill(r["target"]):
                name = friendly("app", r["target"])
                self.store.event("blocked", f"{name} was closed: "
                                 + ("locked by a parent" if r["mode"] == "block" else "over today's limit"))
                self.desktop.notify(f"lock{r['id']}", f"{name} is locked. Ask a parent to unlock it.")


def _mins(seconds: float) -> str:
    m = int(seconds // 60)
    return f"{m // 60} h {m % 60} min" if m >= 60 else f"{m} min"
