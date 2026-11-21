"""The parent's page and its API, on 127.0.0.1 only. Everything but the sign-in needs the
parent password."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from . import rules as R
from .activity import friendly
from .auth import Auth
from .store import Store, today

WEB = Path(__file__).parent / "web"
COOKIE = "kcc_session"
OPEN = {"/", "/api/auth", "/api/setup", "/api/login"}


class Password(BaseModel):
    password: str


class ChangePassword(BaseModel):
    old: str
    new: str


class RuleIn(BaseModel):
    kind: Literal["app", "site"]
    target: str = Field(min_length=1)
    mode: Literal["block", "limit"]
    minutes: int | None = Field(None, ge=1, le=24 * 60)
    browser: str | None = None


class RulePatch(BaseModel):
    enabled: bool | None = None
    minutes: int | None = Field(None, ge=1, le=24 * 60)


class Extra(BaseModel):
    minutes: int = Field(ge=1, le=24 * 60)


def create_app(store: Store, monitor=None, on_stop=None) -> FastAPI:
    app = FastAPI(title="Kid Command Center", docs_url=None, redoc_url=None, openapi_url=None)
    auth = app.state.auth = Auth(store)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = (request.headers.get("host") or "").rsplit(":", 1)[0]
        if host not in ("127.0.0.1", "localhost", "testserver"):
            return JSONResponse({"detail": "forbidden host"}, status_code=403)
        if request.url.path not in OPEN and not request.url.path.startswith("/static"):
            if not auth.valid(request.cookies.get(COOKIE)):
                return JSONResponse({"detail": "sign in first"}, status_code=401)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    def changed():
        if monitor is not None:
            monitor.reload_rules()

    @app.get("/")
    def page():
        return FileResponse(WEB / "index.html")

    # ---- sign in ------------------------------------------------------------------
    @app.get("/api/auth")
    def auth_state(request: Request):
        return {"setup_needed": not auth.has_password,
                "signed_in": auth.valid(request.cookies.get(COOKIE)),
                "wait": auth.locked_for()}

    def _session(response: Response, token: str) -> None:
        response.set_cookie(COOKIE, token, httponly=True, samesite="strict", max_age=8 * 3600)

    @app.post("/api/setup")
    def setup(body: Password, response: Response):
        if auth.has_password:
            raise HTTPException(409, "A password is already set.")
        try:
            auth.set_password(body.password)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        _session(response, auth.login(body.password))
        return {"ok": True}

    @app.post("/api/login")
    def login(body: Password, response: Response):
        wait = auth.locked_for()
        if wait:
            raise HTTPException(429, f"Too many wrong passwords. Try again in {wait} seconds.")
        token = auth.login(body.password)
        if not token:
            raise HTTPException(401, "Wrong password.")
        _session(response, token)
        return {"ok": True}

    @app.post("/api/logout")
    def logout(request: Request, response: Response):
        auth.logout(request.cookies.get(COOKIE))
        response.delete_cookie(COOKIE)
        return {"ok": True}

    @app.post("/api/password")
    def change_password(body: ChangePassword, response: Response):
        if not auth.login(body.old):
            raise HTTPException(401, "The current password is wrong.")
        try:
            auth.set_password(body.new)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        _session(response, auth.login(body.new))
        return {"ok": True}

    # ---- reports ------------------------------------------------------------------
    @app.get("/api/report")
    def report(day: str | None = None):
        day = day or today()
        rows = store.summary(day)
        for r in rows:
            r["name"] = friendly(r["kind"], r["key"])
        opens = store.opens_list(day)
        for o in opens:
            o["name"] = friendly(o["kind"], o["key"])
            o["browser"] = friendly("app", o["exe"]) if o["kind"] == "site" else None
        beat = store.get_meta("heartbeat")
        return {"day": day, "days": sorted(set(store.days()) | {today()}, reverse=True),
                "summary": rows, "opens": opens, "events": store.events_list(day),
                "rules": rules_with_status(),
                "monitor": {"running": bool(beat) and time.time() - float(beat) < 30,
                            "last_seen": float(beat) if beat else None}}

    def rules_with_status() -> list[dict]:
        usage = store.usage(today())
        out = []
        for r in store.rules():
            st = R.status(r, usage.get(r["id"]))
            out.append({**r, **st, "name": friendly(r["kind"], r["target"]),
                        "browser_name": friendly("app", r["browser"]) if r["browser"] else None,
                        "extra_minutes": (usage.get(r["id"]) or {}).get("extra_minutes", 0)})
        return out

    # ---- rules ----------------------------------------------------------------------
    @app.post("/api/rules")
    def add_rule(body: RuleIn):
        target = R.normalize_target(body.kind, body.target)
        if not target or (body.kind == "site" and "." not in target and target != "localhost"):
            raise HTTPException(400, "Enter a site like youtube.com, or a program like roblox.exe.")
        if body.mode == "limit" and not body.minutes:
            raise HTTPException(400, "Set the minutes per day.")
        browser = R.normalize_browser(body.browser) if body.kind == "site" else None
        for r in store.rules():  # one rule per target and browser: update it instead
            if (r["kind"], r["target"], r["browser"]) == (body.kind, target, browser):
                store.update_rule(r["id"], mode=body.mode, minutes=body.minutes, enabled=1)
                changed()
                return {"id": r["id"], "updated": True}
        rid = store.add_rule(body.kind, target, body.mode, body.minutes, browser)
        changed()
        return {"id": rid}

    @app.patch("/api/rules/{rid}")
    def patch_rule(rid: int, body: RulePatch):
        fields = body.model_dump(exclude_none=True)
        if "enabled" in fields:
            fields["enabled"] = int(fields["enabled"])
        store.update_rule(rid, **fields)
        changed()
        return {"ok": True}

    @app.delete("/api/rules/{rid}")
    def delete_rule(rid: int):
        store.delete_rule(rid)
        changed()
        return {"ok": True}

    @app.post("/api/rules/{rid}/extra")
    def extra(rid: int, body: Extra):
        store.grant_extra(today(), rid, body.minutes)
        store.event("extra_time", f"{body.minutes} extra minutes granted today")
        changed()
        return {"ok": True}

    @app.post("/api/monitor/stop")
    def stop():
        if on_stop is None:
            raise HTTPException(503, "Not running as the monitor.")
        on_stop()
        return {"stopping": True}

    return app
