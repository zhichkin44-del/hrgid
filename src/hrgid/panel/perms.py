"""Права по реестру SCREENS.md §1: две роли, действия только для администратора."""

from __future__ import annotations

from typing import Literal

Role = Literal["hr", "admin"]
ROLE_NAMES: dict[str, str] = {"hr": "HR-специалист", "admin": "Администратор"}

# Разделы целиком только для администратора (П9).
ADMIN_SECTIONS = frozenset({"settings"})

# Действия, доступные только администратору (в реестре: «Доступно администратору»).
ADMIN_ACTIONS = frozenset(
    {
        "doc.order",  # П4.3 приказ и дата начала
        "doc.rollback",  # П4.5 откат
        "doc.delete_version",  # П4.6 удаление версии
        "tk.accept_risk",  # П5.3 принять с риском
        "budget.edit",  # П8.1 лимит
        "perf.view",  # П8: скорость, сбои, разбор запросов
        "log.logins",  # П10.4 журнал входов
        "settings.edit",  # П9
        "company.export",  # П9.2
        "company.delete",  # П9.3
    }
)


def can(role: str, action: str) -> bool:
    """Может ли роль выполнить действие. Неизвестная роль не может ничего."""
    if role not in ROLE_NAMES:
        return False
    if action in ADMIN_ACTIONS:
        return role == "admin"
    return True


def can_section(role: str, section: str) -> bool:
    if role not in ROLE_NAMES:
        return False
    return role == "admin" or section not in ADMIN_SECTIONS
