"""Sign-in with a one-time code (SPEC-09 §4.1, §4.8): hash, 5 attempts, 15-minute block; consent."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from hrgid.bot.models import Employee, InviteCode
from hrgid.bot.repo import Repo

MAX_FAILS = 5
BLOCK_MINUTES = 15
_ALPHABET = "АБВГДЕКЛМНПРСТУХ0123456789"
# Latin look-alikes of Cyrillic letters: an employee could type the code in Latin
_LOOKALIKE = str.maketrans("ABEKMHOPCTXY", "АВЕКМНОРСТХУ")
_CODE_RE = re.compile(r"^[A-ZА-ЯЁ0-9]{2,6}-\d{4}-[A-ZА-ЯЁ0-9]{4}$")


def normalize_code(text: str) -> str:
    t = text.strip().upper().replace("–", "-").replace("—", "-").replace(" ", "")
    return t.translate(_LOOKALIKE)


def looks_like_code(text: str) -> bool:
    return bool(_CODE_RE.match(text.strip().upper().replace("–", "-").replace("—", "-")))


LoginStatus = Literal["ok", "bad", "blocked", "closed"]


@dataclass(frozen=True)
class LoginResult:
    status: LoginStatus
    employee: Employee | None = None
    code: InviteCode | None = None
    fails: int = 0  # how many consecutive errors
    blocked_until: datetime | None = None

    @property
    def left(self) -> int:
        return max(MAX_FAILS - self.fails, 0)


class AuthService:
    def __init__(self, repo: Repo, pepper: str | None = None) -> None:
        self.repo = repo
        self._pepper = pepper if pepper is not None else os.environ.get("HRGID_CODE_PEPPER", "")

    def hash_code(self, code: str) -> str:
        return hmac.new(
            self._pepper.encode(), normalize_code(code).encode(), hashlib.sha256
        ).hexdigest()

    def issue_code(self, company_id: str, employee_id: str, now: datetime) -> str:
        """Issues a code (the employee's old codes are revoked). The plain form is shown only in the
        response."""
        for c in self.repo.codes_of(company_id, employee_id):
            if c.status == "active":
                self.repo.save_code(c.model_copy(update={"status": "revoked"}))
        prefix = self.repo.settings(company_id).code_prefix
        tail = "".join(secrets.choice(_ALPHABET) for _ in range(4))
        code = f"{prefix}-{secrets.randbelow(9000) + 1000}-{tail}"
        self.repo.save_code(
            InviteCode(
                id=secrets.token_hex(8),
                company_id=company_id,
                employee_id=employee_id,
                code_hash=self.hash_code(code),
                created_at=now,
            )
        )
        return code

    def revoke_codes(self, company_id: str, employee_id: str) -> None:
        for c in self.repo.codes_of(company_id, employee_id):
            if c.status == "active":
                self.repo.save_code(c.model_copy(update={"status": "revoked"}))

    def blocked_until(self, telegram_id: int, now: datetime) -> datetime | None:
        t = self.repo.throttle(telegram_id)
        if t.blocked_until and t.blocked_until > now:
            return t.blocked_until
        return None

    def check_code(self, telegram_id: int, text: str, now: datetime) -> LoginResult:
        """Checks the code. The code is not consumed here; it is consumed on confirmation (consent
        or sign-in)."""
        until = self.blocked_until(telegram_id, now)
        if until:
            return LoginResult("blocked", blocked_until=until)
        t = self.repo.throttle(telegram_id)
        if t.blocked_until and t.blocked_until <= now:
            t = t.model_copy(update={"fails": 0, "blocked_until": None})
        code = self.repo.code_by_hash(self.hash_code(text))
        emp = self.repo.employee(code.company_id, code.employee_id) if code else None
        if code is None or code.status != "active" or emp is None:
            fails = t.fails + 1
            block = now + timedelta(minutes=BLOCK_MINUTES) if fails >= MAX_FAILS else None
            self.repo.save_throttle(
                t.model_copy(update={"fails": 0 if block else fails, "blocked_until": block})
            )
            return LoginResult("blocked" if block else "bad", fails=fails, blocked_until=block)
        self.repo.save_throttle(t.model_copy(update={"fails": 0, "blocked_until": None}))
        if emp.status != "active":
            return LoginResult("closed", emp, code)
        return LoginResult("ok", emp, code)

    def bind(
        self, emp: Employee, code: InviteCode, telegram_id: int, now: datetime, *, consent: bool
    ) -> tuple[Employee, int | None]:
        """Links Telegram and consumes the code. Returns (employee, previous telegram_id)."""
        old = emp.telegram_id if emp.telegram_id != telegram_id else None
        upd: dict[str, object] = {"telegram_id": telegram_id}
        if consent and emp.consent_at is None:
            upd["consent_at"] = now  # consent time is recorded (§4.1, §9)
        emp = emp.model_copy(update=upd)
        self.repo.save_employee(emp)
        self.repo.save_code(code.model_copy(update={"status": "used", "used_at": now}))
        return emp, old

    def unbind(self, emp: Employee) -> Employee:
        emp = emp.model_copy(update={"telegram_id": None})
        self.repo.save_employee(emp)
        return emp
