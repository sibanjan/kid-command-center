"""Turn once-a-second samples of the front window into sessions (time in front) and opens.

What counts as an open:
  app   the first time a new process of a program comes to the front (switching back to a
        program that is already open is not a new open)
  site  a browser window or tab arriving at a site from somewhere else (another site, a new tab
        page, or a new window); moving between pages of the same site is not a new open
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

BROWSERS = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe"}
GAP = 5.0  # seconds without a sample (sleep, a locked screen) that end a session

FRIENDLY = {
    "chrome.exe": "Google Chrome", "msedge.exe": "Microsoft Edge", "firefox.exe": "Firefox",
    "brave.exe": "Brave", "opera.exe": "Opera", "vivaldi.exe": "Vivaldi",
    "explorer.exe": "File Explorer", "code.exe": "VS Code", "winword.exe": "Word",
    "excel.exe": "Excel", "powerpnt.exe": "PowerPoint", "notepad.exe": "Notepad",
    "minecraft.windows.exe": "Minecraft", "robloxplayerbeta.exe": "Roblox",
    "discord.exe": "Discord", "steam.exe": "Steam", "spotify.exe": "Spotify",
    "applicationframehost.exe": "Windows app", "whatsapp.exe": "WhatsApp",
}


@dataclass
class Sample:
    ts: float
    hwnd: int = 0
    pid: int = 0
    exe: str = ""          # lower-case file name, e.g. chrome.exe
    title: str = ""
    url: str | None = None  # browsers only: what the address bar shows
    locked: bool = False


@dataclass
class Session:
    kind: str
    key: str
    exe: str
    start: float
    end: float
    title: str | None = None
    url: str | None = None
    sid: int | None = None


def domain(url: str | None) -> str | None:
    """youtube.com for https://www.youtube.com/watch?v=..; None for browser pages, text being
    typed, or anything that is not a web address."""
    if not url:
        return None
    url = url.strip()
    if " " in url or url.startswith(("chrome:", "edge:", "about:", "brave:", "opera:", "file:",
                                     "view-source:", "chrome-extension:", "moz-extension:")):
        return None
    if "://" not in url:
        url = "https://" + url
    host = (urlsplit(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if "." not in host and host != "localhost":
        return None
    return host or None


def friendly(kind: str, key: str) -> str:
    if kind == "site":
        return key
    return FRIENDLY.get(key, key[:-4].replace("_", " ").title() if key.endswith(".exe") else key)


def item_of(s: Sample) -> tuple[str, str] | None:
    """(kind, key) of what is in front, or None when nothing is (locked, asleep)."""
    if s.locked or not s.exe:
        return None
    if s.exe in BROWSERS:
        d = domain(s.url)
        if d:
            return ("site", d)
    return ("app", s.exe)


class Tracker:
    def __init__(self, store):
        self.store = store
        self.current: Session | None = None
        self.last_ts: float | None = None
        self.seen_pids: set[tuple[int, str]] = set()
        self.window_site: dict[int, str | None] = {}

    def update(self, s: Sample) -> tuple[str, str] | None:
        """Record one sample; returns the (kind, key) in front, if any."""
        item = item_of(s)
        gap = self.last_ts is None or s.ts - self.last_ts > GAP
        self.last_ts = s.ts

        if item is None or gap:
            self.close()
        if item is None:
            return None
        kind, key = item

        # opens
        if (s.pid, s.exe) not in self.seen_pids:
            self.seen_pids.add((s.pid, s.exe))
            self.store.add_open(s.ts, "app", s.exe, s.exe, s.title, None)
        if s.exe in BROWSERS:
            site = key if kind == "site" else None
            if site and self.window_site.get(s.hwnd) != site:
                self.store.add_open(s.ts, "site", site, s.exe, s.title, s.url)
            self.window_site[s.hwnd] = site

        # time in front
        cur = self.current
        if cur and (cur.kind, cur.key, cur.exe) == (kind, key, s.exe):
            cur.end, cur.title = s.ts, s.title or cur.title
            cur.url = s.url if kind == "site" else None
            self.store.extend_session(cur.sid, cur)
        else:
            self.close()
            self.current = Session(kind, key, s.exe, s.ts, s.ts, s.title,
                                   s.url if kind == "site" else None)
            self.current.sid = self.store.add_session(self.current)
        return item

    def close(self) -> None:
        self.current = None

    def forget_dead(self, alive_pids: set[int]) -> None:
        """Drop closed processes, so a pid reused later counts as a new open."""
        self.seen_pids = {p for p in self.seen_pids if p[0] in alive_pids}
