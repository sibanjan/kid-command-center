"""Blocks (the kill switch) and daily time limits.

A rule names an app (its program file, e.g. roblox.exe) or a site (a domain, e.g. youtube.com,
which also covers m.youtube.com and music.youtube.com). A site rule can be limited to one
browser ("YouTube in Chrome"); without one it applies in every browser.

  block  locked until the parent turns the rule off
  limit  N minutes a day of time in front; then locked until tomorrow, or until the parent
         grants extra minutes for today
"""

from __future__ import annotations

from urllib.parse import urlsplit

WARN_AT = ((5, 1), (1, 2))  # (minutes left, flag bit): warn once at 5 and once at 1 minute left


def normalize_target(kind: str, target: str) -> str:
    t = target.strip().lower()
    if kind == "app":
        t = t.replace("\\", "/").rsplit("/", 1)[-1]
        return t if t.endswith(".exe") else t + ".exe"
    if "://" not in t:
        t = "https://" + t
    host = (urlsplit(t).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def normalize_browser(browser: str | None) -> str | None:
    if not browser:
        return None
    b = browser.strip().lower()
    b = {"edge": "msedge"}.get(b, b)
    return b if b.endswith(".exe") else b + ".exe"


def matches(rule: dict, kind: str, key: str, exe: str) -> bool:
    if rule["kind"] != kind:
        return False
    if kind == "app":
        return key == rule["target"]
    if rule.get("browser") and rule["browser"] != exe:
        return False
    return key == rule["target"] or key.endswith("." + rule["target"])


def status(rule: dict, usage: dict | None) -> dict:
    """Is the rule locking its target now, and how much time is used and left today."""
    used = (usage or {}).get("seconds", 0.0)
    extra = (usage or {}).get("extra_minutes", 0)
    if not rule["enabled"]:
        return {"locked": False, "used": used, "allowed": None, "left": None}
    if rule["mode"] == "block":
        return {"locked": True, "used": used, "allowed": 0, "left": 0}
    allowed = (rule["minutes"] or 0) * 60 + extra * 60
    left = max(0.0, allowed - used)
    return {"locked": left <= 0, "used": used, "allowed": allowed, "left": left}


def due_warning(rule: dict, usage: dict | None) -> tuple[int, int] | None:
    """(minutes left, flag) if a warning should be shown now and has not been yet."""
    if rule["mode"] != "limit" or not rule["enabled"]:
        return None
    st = status(rule, usage)
    warned = (usage or {}).get("warned", 0)
    if st["left"] <= 0:
        return None
    for minutes, flag in sorted(WARN_AT):  # the nearest threshold passed wins
        if st["left"] <= minutes * 60:
            if warned & flag:
                return None
            # also mark the larger thresholds, so a late start does not warn twice
            flags = flag | sum(f for m, f in WARN_AT if m > minutes)
            return minutes, flags
    return None
