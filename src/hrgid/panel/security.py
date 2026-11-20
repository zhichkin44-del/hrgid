"""Вход в панель: почта + одноразовый код, сессии с истечением, журнал входов (SPEC-09 §8).

Правила (SCREENS.md, П0): код — 6 цифр, действует 10 минут, повторно — через 60 секунд,
5 неверных попыток подряд — блок на 15 минут; сессия закрывается через 8 часов без действий.
Код и токен сессии хранятся хэшем.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from hrgid.bot.store import DocStore
from hrgid.panel.mailer import Mailer
from hrgid.panel.perms import Role

K_USER, K_LOGIN, K_SESS, K_LOGLOG = "puser", "plogin", "psess", "plog"
CODE_TTL = timedelta(minutes=10)
RESEND_AFTER = timedelta(seconds=60)
MAX_FAILS = 5
BLOCK = timedelta(minutes=15)
IDLE_LIMIT = timedelta(hours=8)
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def norm_email(s: str) -> str:
    return s.strip().lower()


@dataclass(frozen=True)
class PanelUser:
    email: str
    company_id: str
    name: str
    role: Role
    active: bool = True
    away: bool = False  # в отпуске: назначать обращения нельзя (П2.3)


@dataclass(frozen=True)
class RequestResult:
    status: Literal["sent", "no_access", "bad_email", "too_soon"]
    wait_sec: int = 0


@dataclass(frozen=True)
class VerifyResult:
    status: Literal["ok", "bad", "blocked", "expired", "no_code"]
    token: str = ""
    left: int = 0
    blocked_until: datetime | None = None


@dataclass(frozen=True)
class Session:
    sid: str
    email: str
    company_id: str
    created_at: datetime
    last_seen: datetime


def _user_dict(u: PanelUser) -> dict[str, Any]:
    return {
        "email": u.email,
        "company_id": u.company_id,
        "name": u.name,
        "role": u.role,
        "active": u.active,
        "away": u.away,
    }


def _user(d: dict[str, Any]) -> PanelUser:
    role: Role = "admin" if d["role"] == "admin" else "hr"
    return PanelUser(
        str(d["email"]),
        str(d["company_id"]),
        str(d["name"]),
        role,
        bool(d.get("active", True)),
        bool(d.get("away", False)),
    )


class PanelAuth:
    def __init__(self, store: DocStore, mailer: Mailer, pepper: str | None = None) -> None:
        self.store = store
        self.mailer = mailer
        self._pepper = pepper if pepper is not None else os.environ.get("HRGID_PANEL_PEPPER", "")

    # --- пользователи ------------------------------------------------------
    def add_user(
        self, company_id: str, email: str, name: str, role: Role, *, away: bool = False
    ) -> PanelUser:
        u = PanelUser(norm_email(email), company_id, name, role, True, away)
        self.store.put(K_USER, company_id, u.email, _user_dict(u))
        return u

    def users(self, company_id: str) -> list[PanelUser]:
        return [_user(d) for d in self.store.all(K_USER, company_id)]

    def user_by_email(self, email: str) -> PanelUser | None:
        for _, d in self.store.find(K_USER, "email", norm_email(email)):
            u = _user(d)
            if u.active:
                return u
        return None

    def user(self, company_id: str, email: str) -> PanelUser | None:
        d = self.store.get(K_USER, company_id, norm_email(email))
        return _user(d) if d else None

    # --- журнал входов -----------------------------------------------------
    def _log(self, company: str, now: datetime, email: str, result: str, ip: str) -> None:
        n = self.store.next_seq(company, "plog")
        self.store.put(
            K_LOGLOG,
            company,
            f"{n:010d}",
            {"n": f"{n:010d}", "at": now.isoformat(), "email": email, "result": result, "ip": ip},
        )

    def login_log(self, company: str) -> list[dict[str, str]]:
        rows = [{k: str(v) for k, v in r.items()} for r in self.store.all(K_LOGLOG, company)]
        return sorted(rows, key=lambda r: r["n"], reverse=True)

    # --- код ---------------------------------------------------------------
    def _hash(self, value: str) -> str:
        return hmac.new(self._pepper.encode(), value.encode(), hashlib.sha256).hexdigest()

    def request_code(self, email: str, now: datetime, ip: str = "") -> RequestResult:
        email = norm_email(email)
        if not _EMAIL.match(email):
            return RequestResult("bad_email")
        user = self.user_by_email(email)
        if user is None:
            self._log("", now, email, "no_access", ip)
            return RequestResult("no_access")
        st = self.store.get(K_LOGIN, user.company_id, email) or {}
        sent = st.get("sent_at")
        if sent:
            wait = datetime.fromisoformat(sent) + RESEND_AFTER - now
            if wait.total_seconds() > 0:
                return RequestResult("too_soon", int(wait.total_seconds()) + 1)
        code = f"{secrets.randbelow(1_000_000):06d}"
        keep = {k: st[k] for k in ("blocked_until",) if k in st}
        self.store.put(
            K_LOGIN,
            user.company_id,
            email,
            {
                **keep,
                "code_hash": self._hash(code),
                "expires": (now + CODE_TTL).isoformat(),
                "sent_at": now.isoformat(),
                "fails": 0,
            },
        )
        self.mailer.send(
            email, "Код входа в HRgid", f"Код для входа в панель: {code}\nДействует 10 минут."
        )
        self._log(user.company_id, now, email, "code_sent", ip)
        return RequestResult("sent")

    def verify(self, email: str, code: str, now: datetime, ip: str = "") -> VerifyResult:
        email = norm_email(email)
        user = self.user_by_email(email)
        if user is None:
            return VerifyResult("no_code")
        co = user.company_id
        st = self.store.get(K_LOGIN, co, email)
        if st is None:
            return VerifyResult("no_code")
        until = st.get("blocked_until")
        if until and datetime.fromisoformat(until) > now:
            return VerifyResult("blocked", blocked_until=datetime.fromisoformat(until))
        if not st.get("code_hash"):
            return VerifyResult("no_code")
        if datetime.fromisoformat(st["expires"]) < now:
            return VerifyResult("expired")
        if not hmac.compare_digest(self._hash(code.strip()), st["code_hash"]):
            fails = int(st.get("fails", 0)) + 1
            if fails >= MAX_FAILS:
                until_dt = now + BLOCK
                self.store.put(K_LOGIN, co, email, {"blocked_until": until_dt.isoformat()})
                self._log(co, now, email, "blocked", ip)
                return VerifyResult("blocked", blocked_until=until_dt)
            self.store.put(K_LOGIN, co, email, {**st, "fails": fails})
            self._log(co, now, email, "bad_code", ip)
            return VerifyResult("bad", left=MAX_FAILS - fails)
        self.store.put(K_LOGIN, co, email, {})  # код одноразовый
        token = secrets.token_urlsafe(32)
        sid = self._hash("sid:" + token)
        self.store.put(
            K_SESS,
            co,
            sid,
            {
                "sid": sid,
                "email": email,
                "company_id": co,
                "created_at": now.isoformat(),
                "last_seen": now.isoformat(),
            },
        )
        self._log(co, now, email, "ok", ip)
        return VerifyResult("ok", token=token)

    # --- сессии ------------------------------------------------------------
    def session(
        self, token: str, now: datetime
    ) -> tuple[Session, PanelUser] | Literal["expired"] | None:
        """Сессия по токену: None — нет такой; 'expired' — закрыта по бездействию."""
        if not token:
            return None
        sid = self._hash("sid:" + token)
        found = self.store.find(K_SESS, "sid", sid)
        if not found:
            return None
        co, d = found[0]
        s = Session(
            sid,
            d["email"],
            co,
            datetime.fromisoformat(d["created_at"]),
            datetime.fromisoformat(d["last_seen"]),
        )
        if now - s.last_seen > IDLE_LIMIT:
            self.store.delete(K_SESS, co, sid)
            self._log(co, now, s.email, "session_expired", "")
            return "expired"
        user = self.user(co, s.email)
        if user is None or not user.active:
            self.store.delete(K_SESS, co, sid)
            return None
        if now - s.last_seen > timedelta(seconds=30):
            d["last_seen"] = now.isoformat()
            self.store.put(K_SESS, co, sid, d)
        return s, user

    def logout(self, token: str, now: datetime) -> None:
        sid = self._hash("sid:" + token)
        for co, d in self.store.find(K_SESS, "sid", sid):
            self.store.delete(K_SESS, co, sid)
            self._log(co, now, d["email"], "logout", "")

    def drop_company_sessions(self, company_id: str) -> None:
        for d in self.store.all(K_SESS, company_id):
            self.store.delete(K_SESS, company_id, d["sid"])
