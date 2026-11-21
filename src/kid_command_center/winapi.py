"""The few Windows calls the monitor needs, through ctypes and UI Automation (comtypes).
Nothing here installs a hook, an extension or a policy: it reads the front window, reads a
browser's address bar the way a screen reader does, ends a program, closes a browser tab, and
shows a message box."""

from __future__ import annotations

import ctypes
import logging
import threading
import time
from ctypes import wintypes

import psutil

from .activity import BROWSERS, Sample

log = logging.getLogger(__name__)
user32 = ctypes.windll.user32 if hasattr(ctypes, "windll") else None

MB_ICONINFORMATION, MB_TOPMOST, MB_SETFOREGROUND, MB_SYSTEMMODAL = 0x40, 0x40000, 0x10000, 0x1000
VK_CONTROL, VK_W, KEYEVENTF_KEYUP = 0x11, 0x57, 0x2


def _title(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def screen_locked() -> bool:
    """True on the lock screen or a secure desktop (where the input desktop can't be opened)."""
    h = user32.OpenInputDesktop(0, False, 0x0100)  # DESKTOP_SWITCHDESKTOP
    if not h:
        return True
    user32.CloseDesktop(h)
    return False


class AddressBar:
    """Reads the URL in a browser window's address bar with UI Automation."""

    def __init__(self):
        self._uia = None
        self._cache: dict[int, object] = {}

    def _init(self):
        import comtypes.client

        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as U

        self.U = U
        self._uia = comtypes.client.CreateObject(U.CUIAutomation._reg_clsid_, interface=U.IUIAutomation)
        self._edit = self._uia.CreatePropertyCondition(U.UIA_ControlTypePropertyId, U.UIA_EditControlTypeId)

    def url(self, hwnd: int) -> str | None:
        """The address shown, or None while the child is typing in it (that is a search,
        not a page) or when it cannot be read."""
        try:
            if self._uia is None:
                self._init()
            box = self._cache.get(hwnd)
            if box is None:
                box = self._uia.ElementFromHandle(hwnd).FindFirst(self.U.TreeScope_Descendants, self._edit)
                if box is None:
                    return None
                if len(self._cache) > 50:
                    self._cache.clear()
                self._cache[hwnd] = box
            if box.GetCurrentPropertyValue(self.U.UIA_HasKeyboardFocusPropertyId):
                return None
            value = box.GetCurrentPropertyValue(self.U.UIA_ValueValuePropertyId)
            return str(value) if value else None
        except Exception as exc:  # window closed, browser busy: try again next second
            self._cache.pop(hwnd, None)
            log.debug("address bar not readable: %s", exc)
            return None


class Desktop:
    def __init__(self):
        self.bar = AddressBar()
        self._last_url: dict[int, str] = {}
        self._notified: dict[str, float] = {}

    def sample(self) -> Sample:
        now = time.time()
        if screen_locked():
            return Sample(now, locked=True)
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return Sample(now, locked=True)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        try:
            exe = psutil.Process(pid.value).name().lower()
        except psutil.Error:
            return Sample(now, hwnd=hwnd, pid=pid.value)
        if exe == "lockapp.exe":
            return Sample(now, locked=True)
        url = None
        if exe in BROWSERS:
            url = self.bar.url(hwnd)
            if url is None:  # typing in the address bar: still on the page it was on
                url = self._last_url.get(hwnd)
            else:
                self._last_url[hwnd] = url
        return Sample(now, hwnd=hwnd, pid=pid.value, exe=exe, title=_title(hwnd), url=url)

    def foreground(self) -> int:
        return user32.GetForegroundWindow()

    def close_tab(self, hwnd: int) -> bool:
        """Ctrl+W in the browser window, only while it is the one in front."""
        if user32.GetForegroundWindow() != hwnd:
            return False
        for key, flags in ((VK_CONTROL, 0), (VK_W, 0), (VK_W, KEYEVENTF_KEYUP), (VK_CONTROL, KEYEVENTF_KEYUP)):
            user32.keybd_event(key, 0, flags, 0)
        self._last_url.pop(hwnd, None)
        return True

    def kill(self, exe: str) -> int:
        """End every running process of a program; returns how many."""
        n = 0
        for p in psutil.process_iter(["name"]):
            if (p.info["name"] or "").lower() == exe:
                try:
                    p.kill()
                    n += 1
                except psutil.Error:
                    pass
        return n

    def running(self) -> set[str]:
        return {(p.info["name"] or "").lower() for p in psutil.process_iter(["name"])}

    def pids(self) -> set[int]:
        return set(psutil.pids())

    def notify(self, key: str, text: str, every: float = 30) -> None:
        """A message box on top of everything, at most once per `every` seconds per key."""
        now = time.time()
        if now - self._notified.get(key, 0) < every:
            return
        self._notified[key] = now
        flags = MB_ICONINFORMATION | MB_TOPMOST | MB_SETFOREGROUND | MB_SYSTEMMODAL
        threading.Thread(target=user32.MessageBoxW, args=(0, text, "Kid Command Center", flags),
                         daemon=True).start()
