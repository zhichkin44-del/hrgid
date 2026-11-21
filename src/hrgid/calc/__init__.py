"""Deterministic calculations R1-R9 (Labor Code of the RF, Federal Law 255-FZ). Models are not
called."""

from hrgid.calc.common import CalcError, CalcResult
from hrgid.calc.overtime import overtime_pay, weekend_work_pay
from hrgid.calc.sick import sick_pay
from hrgid.calc.terms import last_working_day, payment_deadline, probation_end
from hrgid.calc.vacation import accrued_vacation, leave_compensation, vacation_pay

__all__ = [
    "CalcError",
    "CalcResult",
    "accrued_vacation",
    "last_working_day",
    "leave_compensation",
    "overtime_pay",
    "payment_deadline",
    "probation_end",
    "sick_pay",
    "vacation_pay",
    "weekend_work_pay",
]
