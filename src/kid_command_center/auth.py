"""The parent password: a salted PBKDF2 hash in the database (never the password itself),
sign-in sessions held in memory, and a pause after repeated wrong guesses."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

ITERATIONS = 300_000
SESSION_HOURS = 8
MAX_FAILS, LOCKOUT = 5, 120  # five wrong passwords: wait two minutes


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        _, iters, salt, digest = stored.split("$")
        got = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iters))
    except ValueError:
        return False
    return hmac.compare_digest(got.hex(), digest)


class Auth:
    def __init__(self, store):
        self.store = store
        self.sessions: dict[str, float] = {}
        self.fails: list[float] = []

    @property
    def has_password(self) -> bool:
        return bool(self.store.get_meta("password"))

    def set_password(self, password: str) -> None:
        if len(password) < 6:
            raise ValueError("Use at least 6 characters.")
        self.store.set_meta("password", hash_password(password))
        self.sessions.clear()  # everyone signs in again with the new password

    def locked_for(self) -> int:
        now = time.time()
        self.fails = [t for t in self.fails if now - t < LOCKOUT]
        return int(LOCKOUT - (now - self.fails[0])) + 1 if len(self.fails) >= MAX_FAILS else 0

    def login(self, password: str) -> str | None:
        if self.locked_for():
            return None
        if check_password(password, self.store.get_meta("password") or ""):
            self.fails.clear()
            token = secrets.token_urlsafe(32)
            self.sessions[token] = time.time() + SESSION_HOURS * 3600
            return token
        self.fails.append(time.time())
        return None

    def valid(self, token: str | None) -> bool:
        exp = self.sessions.get(token or "")
        if exp and exp > time.time():
            return True
        self.sessions.pop(token or "", None)
        return False

    def logout(self, token: str | None) -> None:
        self.sessions.pop(token or "", None)
