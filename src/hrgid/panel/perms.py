"""Rights per the SCREENS.md §1 registry: two roles, actions for the administrator only."""

from __future__ import annotations

from typing import Literal

Role = Literal["hr", "admin"]
ROLE_NAMES: dict[str, str] = {"hr": "HR-специалист", "admin": "Администратор"}

# Sections entirely for the administrator only (П9).
ADMIN_SECTIONS = frozenset({"settings"})

# Actions available only to the administrator (in the registry: "Available to the administrator").
ADMIN_ACTIONS = frozenset(
    {
        "doc.order",  # П4.3 order and start date
        "doc.rollback",  # П4.5 rollback
        "doc.delete_version",  # П4.6 version deletion
        "tk.accept_risk",  # П5.3 accept with risk
        "budget.edit",  # П8.1 limit
        "perf.view",  # П8: speed, failures, request analysis
        "log.logins",  # П10.4 sign-in log
        "settings.edit",  # П9
        "company.export",  # П9.2
        "company.delete",  # П9.3
    }
)


def can(role: str, action: str) -> bool:
    """Whether a role may perform an action. An unknown role can do nothing."""
    if role not in ROLE_NAMES:
        return False
    if action in ADMIN_ACTIONS:
        return role == "admin"
    return True


def can_section(role: str, section: str) -> bool:
    if role not in ROLE_NAMES:
        return False
    return role == "admin" or section not in ADMIN_SECTIONS
