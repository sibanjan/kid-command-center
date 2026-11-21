import pytest
from fastapi.testclient import TestClient

from kid_command_center import rules as R
from kid_command_center.activity import Sample, Tracker, domain
from kid_command_center.monitor import Monitor
from kid_command_center.store import Store, day_of
from kid_command_center.web import create_app

T0 = 1_800_000_000.0  # a fixed moment, so the day is fixed


class FakeDesktop:
    def __init__(self):
        self.killed, self.closed, self.notes = [], [], []
        self.procs = {"explorer.exe"}

    def kill(self, exe):
        if exe in self.procs:
            self.procs.discard(exe)
            self.killed.append(exe)
            return 1
        return 0

    def close_tab(self, hwnd):
        self.closed.append(hwnd)
        return True

    def running(self):
        return set(self.procs)

    def pids(self):
        return {1, 2, 3, 10, 11}

    def notify(self, key, text, every=30):
        self.notes.append(text)


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "a.db")


def chrome(ts, url, title="t", hwnd=10, pid=1):
    return Sample(ts, hwnd=hwnd, pid=pid, exe="chrome.exe", title=title, url=url)


def app(ts, exe, pid, title="x", hwnd=20):
    return Sample(ts, hwnd=hwnd, pid=pid, exe=exe, title=title)


def test_domain():
    assert domain("https://www.youtube.com/watch?v=1") == "youtube.com"
    assert domain("m.youtube.com/shorts") == "m.youtube.com"
    assert domain("chrome://newtab/") is None
    assert domain("how to draw a cat") is None  # text typed in the address bar
    assert domain("localhost:8790/x") == "localhost"


def test_tracker_counts_opens_and_time(store):
    t = Tracker(store)
    for i in range(10):                       # 9 s on YouTube
        t.update(chrome(T0 + i, "https://www.youtube.com/watch?v=a"))
    t.update(chrome(T0 + 10, "https://youtube.com/watch?v=b"))   # same site: not a new open
    t.update(app(T0 + 11, "code.exe", 2))
    t.update(chrome(T0 + 12, "https://youtube.com/watch?v=b"))   # back to the same tab: not new
    t.update(chrome(T0 + 13, "chrome://newtab/"))
    t.update(chrome(T0 + 14, "https://youtube.com/"))            # opened again from a new tab
    t.update(app(T0 + 15, "code.exe", 2))                         # same process: not a new open
    t.update(app(T0 + 16, "code.exe", 3))                         # a second window process: new
    rows = {(r["kind"], r["key"]): r for r in store.summary(day_of(T0))}
    assert rows[("site", "youtube.com")]["opens"] == 2
    assert rows[("site", "youtube.com")]["seconds"] == 10
    assert rows[("app", "chrome.exe")]["opens"] == 1
    assert rows[("app", "code.exe")]["opens"] == 2
    kinds = [o["kind"] + ":" + o["key"] for o in reversed(store.opens_list(day_of(T0)))]
    assert kinds[:3] == ["app:chrome.exe", "site:youtube.com", "app:code.exe"]


def test_locked_screen_and_gaps_are_not_time(store):
    t = Tracker(store)
    t.update(app(T0, "code.exe", 2))
    t.update(app(T0 + 1, "code.exe", 2))
    t.update(Sample(T0 + 2, locked=True))
    t.update(app(T0 + 600, "code.exe", 2))      # back after the lock: new session, same open
    t.update(app(T0 + 601, "code.exe", 2))
    row = store.summary(day_of(T0))[0]
    assert row["seconds"] == 2 and row["opens"] == 1


def test_rules_match_and_status():
    r = {"kind": "site", "target": "youtube.com", "browser": "chrome.exe", "mode": "limit",
         "minutes": 30, "enabled": 1, "id": 1}
    assert R.matches(r, "site", "m.youtube.com", "chrome.exe")
    assert not R.matches(r, "site", "m.youtube.com", "msedge.exe")       # Chrome only
    assert not R.matches(r, "site", "notyoutube.com", "chrome.exe")
    assert R.status(r, {"seconds": 1799})["locked"] is False
    assert R.status(r, {"seconds": 1800})["locked"] is True
    assert R.status(r, {"seconds": 1800, "extra_minutes": 15})["left"] == 900
    assert R.normalize_target("app", r"C:\Games\Roblox") == "roblox.exe"
    assert R.normalize_target("site", "https://www.YouTube.com/watch") == "youtube.com"
    assert R.due_warning(r, {"seconds": 1800 - 299}) == (5, 1)
    assert R.due_warning(r, {"seconds": 1800 - 299, "warned": 1}) is None
    assert R.due_warning(r, {"seconds": 1800 - 30}) == (1, 3)  # a late start warns once


def test_youtube_in_chrome_limit_then_closed(store):
    store.add_rule("site", "youtube.com", "limit", 1, "chrome.exe")  # 1 minute a day
    d = FakeDesktop()
    m = Monitor(store, d)
    for i in range(62):
        m.step(chrome(T0 + i, "https://youtube.com/watch?v=x"))
    assert d.closed  # the tab was closed after 60 s in front
    assert any("Time is up for youtube.com" in n for n in d.notes)
    assert any("1 minute left" in n for n in d.notes)
    ev = [e["kind"] for e in store.events_list(day_of(T0))]
    assert "limit_reached" in ev and "warning" in ev
    # YouTube in Edge is not limited by a Chrome-only rule
    d.closed.clear()
    m.step(Sample(T0 + 70, hwnd=30, pid=4, exe="msedge.exe", title="YT", url="https://youtube.com/"))
    assert d.closed == []


def test_parent_extra_time_reopens(store):
    rid = store.add_rule("site", "youtube.com", "limit", 1)
    d = FakeDesktop()
    m = Monitor(store, d)
    for i in range(62):
        m.step(chrome(T0 + i, "https://youtube.com/"))
    store.grant_extra(day_of(T0), rid, 15)
    m.reload_rules()
    d.closed.clear()
    m.step(chrome(T0 + 63, "https://youtube.com/"))
    assert d.closed == []


def test_locked_app_is_ended_even_in_background(store):
    store.add_rule("app", "roblox.exe", "block")
    d = FakeDesktop()
    d.procs.add("roblox.exe")
    m = Monitor(store, d)
    m.step(app(T0, "code.exe", 2))  # roblox is not in front
    assert d.killed == ["roblox.exe"]
    assert any("Roblox is locked" in n for n in d.notes)


def test_unlocked_rule_does_nothing(store):
    rid = store.add_rule("app", "roblox.exe", "block")
    store.update_rule(rid, enabled=0)
    d = FakeDesktop()
    d.procs.add("roblox.exe")
    Monitor(store, d).step(app(T0, "roblox.exe", 5))
    assert d.killed == []


def test_gap_is_logged_when_monitor_was_ended(store):
    store.set_meta("heartbeat", str(T0))
    store.set_meta("clean_stop", "0")
    Monitor(store, FakeDesktop()).start_note(now=T0 + 3600)
    assert any(e["kind"] == "gap" and "1 h 0 min" in e["detail"] for e in store.events_list(day_of(T0)))


# ---- the parent's page ------------------------------------------------------------
@pytest.fixture
def client(store):
    return TestClient(create_app(store))


def test_everything_needs_the_password(client, store):
    assert client.get("/api/report").status_code == 401
    assert client.get("/api/auth").json()["setup_needed"] is True
    assert client.post("/api/setup", json={"password": "123"}).status_code == 400
    assert client.post("/api/setup", json={"password": "secret-1"}).status_code == 200
    assert "secret-1" not in (store.get_meta("password") or "")  # only a hash is kept
    assert client.get("/api/report").status_code == 200
    assert client.post("/api/setup", json={"password": "other-pw"}).status_code == 409
    client.post("/api/logout")
    assert client.get("/api/report").status_code == 401
    assert client.post("/api/login", json={"password": "wrong"}).status_code == 401
    assert client.post("/api/login", json={"password": "secret-1"}).status_code == 200


def test_too_many_wrong_passwords_pause_sign_in(client):
    client.post("/api/setup", json={"password": "secret-1"})
    client.post("/api/logout")
    for _ in range(5):
        client.post("/api/login", json={"password": "nope"})
    r = client.post("/api/login", json={"password": "secret-1"})
    assert r.status_code == 429


def test_other_hosts_are_refused(store):
    c = TestClient(create_app(store), base_url="http://evil.example")
    assert c.get("/api/auth").status_code == 403


def test_stop_needs_the_parent_password(tmp_path, monkeypatch):
    from kid_command_center import __main__ as cli
    from kid_command_center.auth import hash_password

    monkeypatch.setenv("KCC_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "port_busy", lambda port=None: False)
    Store().set_meta("password", hash_password("secret-1"))
    assert cli.stop("wrong") == 2
    assert Store().get_meta("clean_stop") is None  # nothing was changed
    assert cli.stop("secret-1") == 0
    assert Store().get_meta("clean_stop") == "1"  # the watchdog will not restart it


def test_rules_api(client):
    client.post("/api/setup", json={"password": "secret-1"})
    r = client.post("/api/rules", json={"kind": "site", "target": "https://www.youtube.com/",
                                        "mode": "limit", "minutes": 30, "browser": "chrome"})
    rid = r.json()["id"]
    again = client.post("/api/rules", json={"kind": "site", "target": "youtube.com", "mode": "limit",
                                            "minutes": 45, "browser": "chrome"})
    assert again.json() == {"id": rid, "updated": True}
    assert client.post("/api/rules", json={"kind": "site", "target": "notasite", "mode": "block"}).status_code == 400
    client.post(f"/api/rules/{rid}/extra", json={"minutes": 15})
    rules = client.get("/api/report").json()["rules"]
    assert rules[0]["target"] == "youtube.com" and rules[0]["browser"] == "chrome.exe"
    assert rules[0]["minutes"] == 45 and rules[0]["allowed"] == 60 * 60
    client.patch(f"/api/rules/{rid}", json={"enabled": False})
    assert client.get("/api/report").json()["rules"][0]["locked"] is False
    client.delete(f"/api/rules/{rid}")
    assert client.get("/api/report").json()["rules"] == []
