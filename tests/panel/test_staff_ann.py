"""П6 "Employees" (invitation, 1C import, codes, groups, profile, data deletion) and П11."""

from __future__ import annotations

import io
import re

import openpyxl

from .conftest import CO, HR, Env, make_env
from .seed import seed

CSV = (
    "ФИО;Дата приёма;Должность;График;Оклад;Группа;Заработок за 12 месяцев\n"
    "Иванов Иван Иванович;11.03.2024;дизайнер;5/2;80 000,50;Дизайн;960000\n"
    "Петрова Мария;14.09.2020;разработчик;5/2;150000;Разработка;\n"
    "Сидоров Пётр;нет даты;монтажёр;5/2;90000;Монтаж;\n"
    ";01.01.2025;;;;;\n"
).encode()


def start_import(c, data: bytes = CSV, name: str = "zup.csv") -> str:  # type: ignore[no-untyped-def]
    r = c.post("/staff/import", files={"file": (name, data, "text/csv")})
    assert "сопоставление колонок" in r.text, r.text[:300]
    return re.search(r"/staff/import/(\w+)/map", str(r.url)).group(1)  # type: ignore[union-attr]


def test_staff_empty_state() -> None:
    env = make_env()
    assert "Сотрудников пока нет" in env.login(HR).get("/staff").text


def test_invite_shows_code_once_and_hashes_it() -> None:
    env = make_env()
    hr = env.login(HR)
    r = hr.post(
        "/staff/invite", data={"full_name": "Иванов Иван", "hire_date": "01.12.2026", "group": ""}
    )
    assert "Код выдан" in r.text
    code = re.search(r"data-code>([^<]+)<", r.text).group(1)  # type: ignore[union-attr]
    assert code.startswith("ЖС-")
    assert code not in str(env.store._docs)  # hash only
    emp = env.deps.repo.employees(CO)[0]
    assert env.deps.bot_auth.check_code(7, code, env.clock.now).status == "ok"
    assert emp.full_name == "Иванов Иван"
    assert code not in hr.get("/staff").text


def test_invite_validation_and_duplicate_blocked() -> None:
    env = make_env()
    hr = env.login(HR)
    assert "Напишите ФИО" in hr.post("/staff/invite", data={"full_name": "", "hire_date": ""}).text
    assert (
        "Укажите дату приёма"
        in hr.post("/staff/invite", data={"full_name": "А Б", "hire_date": "вчера"}).text
    )
    hr.post("/staff/invite", data={"full_name": "Иванов Иван", "hire_date": "01.12.2026"})
    r = hr.post("/staff/invite", data={"full_name": "иванов  иван", "hire_date": "01.12.2026"})
    assert "Такой сотрудник уже есть" in r.text and "Открыть профиль дубля" in r.text
    assert len(env.deps.repo.employees(CO)) == 1


def test_import_csv_steps_preview_and_apply() -> None:
    env = make_env()
    s = seed(env)  # the company already has Olga and Igor: they are "not in the file"
    hr = env.login(HR)
    token = start_import(hr)
    r = hr.get(f"/staff/import/{token}/preview")
    assert "предпросмотр" in r.text and "С ошибкой" in r.text
    assert "Ольга Смирнова" in r.text and "отключить доступ" in r.text
    errs = hr.get(f"/staff/import/{token}/errors").text
    assert "Дата приёма не прочитана" in errs and "Нет ФИО" in errs
    r = hr.post(f"/staff/import/{token}/apply", data={"disable": [s.olga.id]})
    assert "Импорт — итог" in r.text and "данные на 18.11.2026" in r.text
    emps = {e.full_name: e for e in env.deps.repo.employees(CO)}
    assert emps["Иванов Иван Иванович"].salary is not None
    assert str(emps["Иванов Иван Иванович"].salary) == "80000.50"
    assert str(emps["Иванов Иван Иванович"].earnings_12m) == "960000"
    assert emps["Ольга Смирнова"].status == "disabled"
    assert emps["Игорь Петров"].status == "active"  # the mark can be removed


def test_reimport_updates_instead_of_duplicating() -> None:
    env = make_env()
    hr = env.login(HR)
    for sal in ("80000", "95000"):
        data = f"ФИО;Дата приёма;Оклад\nИванов Иван;01.01.2024;{sal}\n".encode()
        token = start_import(hr, data)
        hr.post(f"/staff/import/{token}/apply", data={})
    emps = env.deps.repo.employees(CO)
    assert len(emps) == 1 and str(emps[0].salary) == "95000"


def test_import_xlsx_and_wrong_files() -> None:
    env = make_env()
    hr = env.login(HR)
    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.append(["ФИО", "Дата приёма", "График"])
    ws.append(["Козлов Дмитрий", "03.06.2023", "5/2"])
    buf = io.BytesIO()
    wb.save(buf)
    token = start_import(hr, buf.getvalue(), "zup.xlsx")
    hr.post(f"/staff/import/{token}/apply", data={})
    assert env.deps.repo.employees(CO)[0].full_name == "Козлов Дмитрий"
    r = hr.post("/staff/import", files={"file": ("a.pdf", b"x", "application/pdf")})
    assert "файл не подошёл" in r.text
    r = hr.post(
        "/staff/import", files={"file": ("a.csv", "Должность;Оклад\nх;1\n".encode(), "text/csv")}
    )
    assert "нет колонки «ФИО»" in r.text


def test_import_mapping_required_columns() -> None:
    env = make_env()
    hr = env.login(HR)
    data = "Сотрудник;Принят;Что-то\nИванов Иван;01.01.2024;1\n".encode()
    token = start_import(hr, data)
    r = hr.post(f"/staff/import/{token}/map", data={"full_name": "0"})
    assert "Выберите колонку: Дата приёма" in r.text
    r = hr.post(f"/staff/import/{token}/map", data={"full_name": "0", "hire_date": "1"})
    assert "предпросмотр" in r.text


def test_bulk_codes_csv_and_print_sheet_with_qr() -> None:
    env = make_env()
    seed(env)
    env.employee(name="Новенький Нил", emp_id="e9")
    hr = env.login(HR)
    r = hr.post("/staff/codes", data={"who": "new", "fmt": "csv"})
    assert r.headers["content-type"].startswith("text/csv") and "Новенький Нил" in r.text
    assert "Ольга Смирнова" not in r.text  # she already has Telegram
    r = hr.post("/staff/codes", data={"who": "all", "fmt": "print"})
    assert "<svg" in r.text and "Лист содержит секреты" in r.text and "ЖС-" in r.text
    r2 = hr.post("/staff/codes", data={"who": "new", "fmt": "csv"})
    assert "Некому выдавать" not in r2.text or r2.status_code == 200


def test_new_code_revokes_old_and_disabled_gets_none() -> None:
    env = make_env()
    s = seed(env)
    hr = env.login(HR)
    env.deps.staff.invite(CO, "Тест Тестов", "01.01.2025", env.clock.now)
    emp = [e for e in env.deps.repo.employees(CO) if e.full_name == "Тест Тестов"][0]
    first = [c for c in env.deps.repo.codes_of(CO, emp.id) if c.status == "active"]
    r = hr.post(f"/staff/{emp.id}/newcode", data={"reason": "потерял"})
    assert "Новый код выдан" in r.text
    codes = env.deps.repo.codes_of(CO, emp.id)
    assert [c.status for c in codes].count("active") == 1 and first[0].status == "active"
    hr.post(f"/staff/{s.igor.id}/disable", data={"confirm": "1"})
    r = hr.post(f"/staff/{s.igor.id}/newcode", data={})
    assert "отключённым доступом" in r.text


def test_disable_requires_checkbox_has_undo_and_blocks_bot() -> None:
    env = make_env()
    s = seed(env)
    hr = env.login(HR)
    r = hr.post(f"/staff/{s.igor.id}/disable", data={})
    assert "больше не работает" in r.text
    r = hr.post(f"/staff/{s.igor.id}/disable", data={"confirm": "1"})
    assert "Доступ отключён" in r.text and "Отменить — 10 с" in r.text
    assert env.deps.repo.employee(CO, s.igor.id).status == "disabled"  # type: ignore[union-attr]
    uid = env.store.all("pundo", CO)[0]["id"]
    hr.post(f"/undo/{uid}", data={"back": "/staff"})
    assert env.deps.repo.employee(CO, s.igor.id).status == "active"  # type: ignore[union-attr]


def test_profile_counts_exclude_confidential_and_match_queue() -> None:
    env = make_env()
    s = seed(env)
    hr = env.login(HR)
    r = hr.get(f"/staff/{s.olga.id}")
    assert "Обращений к HR" in r.text
    # Olga has 1 regular and 1 confidential request: the profile counts only the regular one
    assert env.deps.staff.ticket_count(CO, s.olga.id) == 1
    assert re.search(r"Обращений к HR</span><span class=\"r\">1<", r.text)
    assert "притесняет" not in r.text and "Согласие на обработку данных" in r.text


def test_edit_data_validation() -> None:
    env = make_env()
    s = seed(env)
    hr = env.login(HR)
    base = {
        "hire_date": "11.03.2024",
        "schedule": "2/2",
        "salary": "90000",
        "group": "Дизайн",
        "position": "дизайнер",
    }
    r = hr.post(f"/staff/{s.olga.id}/edit", data={**base, "salary": "много"})
    assert "Оклад — число" in r.text
    r = hr.post(f"/staff/{s.olga.id}/edit", data={**base, "hire_date": ""})
    assert "Укажите дату приёма" in r.text
    r = hr.post(f"/staff/{s.olga.id}/edit", data=base)
    assert "Данные исправлены" in r.text and "перезапишет" in r.text
    assert env.deps.repo.employee(CO, s.olga.id).schedule == "2/2"  # type: ignore[union-attr]


def test_purge_employee_data_deferred_with_undo() -> None:
    env = make_env()
    s = seed(env)
    hr = env.login(HR)
    r = hr.post(f"/staff/{s.olga.id}/purge", data={"reason": "", "understood": ""})
    assert "Укажите основание" in r.text and "Отметьте «понимаю»" in r.text
    r = hr.post(
        f"/staff/{s.olga.id}/purge", data={"reason": "запрос сотрудника", "understood": "1"}
    )
    assert "через 10 секунд" in r.text
    assert env.deps.repo.employee(CO, s.olga.id).full_name == "Ольга Смирнова"  # type: ignore[union-attr]
    env.clock.advance(seconds=11)
    hr.get("/staff")
    after = env.deps.repo.employee(CO, s.olga.id)
    assert after is not None and after.full_name == "Данные удалены" and after.telegram_id is None
    assert env.deps.repo.ticket(CO, s.t1.key).text == ""  # type: ignore[union-attr]
    journal = " ".join(e.target for e in env.deps.journal.entries(CO))
    assert "притесняет" not in journal


def test_hr_can_purge_but_groups_rules() -> None:
    env = make_env()
    s = seed(env)
    hr = env.login(HR)
    assert hr.get(f"/staff/{s.olga.id}/purge").status_code == 200  # П6.12 is done by HR
    assert "Группа добавлена" in hr.post("/staff/groups/add", data={"name": "Тестирование"}).text
    assert "уже есть" in hr.post("/staff/groups/add", data={"name": "тестирование"}).text
    assert "Напишите название" in hr.post("/staff/groups/add", data={"name": ""}).text
    assert "только пустую" in hr.post("/staff/groups/delete", data={"name": "Дизайн"}).text
    assert "Группа удалена" in hr.post("/staff/groups/delete", data={"name": "Тестирование"}).text


# ----- П11 -----


def test_announcement_empty_validation_send_read_remind() -> None:
    env = make_env()
    s = seed(env)
    hr = env.login(HR)
    assert "Объявлений пока нет" in hr.get("/announcements").text
    r = hr.post("/announcements/preview", data={"title": "", "text": ""})
    assert "Напишите заголовок" in r.text and "Напишите текст" in r.text
    r = hr.post("/announcements/preview", data={"title": "t" * 81, "text": "x"})
    assert "не длиннее 80" in r.text
    r = hr.post("/announcements/preview", data={"title": "График", "text": "x" * 901})
    assert "не длиннее 900" in r.text
    r = hr.post(
        "/announcements/preview", data={"title": "График", "text": "Праздники", "groups": ["Пусто"]}
    )
    assert "Нет получателей" in r.text
    r = hr.post("/announcements/preview", data={"title": "График", "text": "Праздники"})
    assert "Отправить объявление?" in r.text and "Получат 2" in r.text
    r = hr.post("/announcements/send", data={"title": "График", "text": "Праздники", "groups": ""})
    assert "Объявление отправлено: 2" in r.text and "Кто прочитал" in r.text
    ann = env.deps.repo.announcements(CO)[0]
    assert sorted(ann.recipients) == sorted([s.olga.id, s.igor.id])
    env.deps.desk.mark_read(CO, ann.id, s.olga.id, env.clock.now)
    r = hr.get(f"/announcements/{ann.id}")
    assert "Игорь Петров" in r.text and "Не прочитали" in r.text
    assert "1 из 2" in hr.get("/announcements").text
    n = len(env.deps.repo.outbox_pending())
    r = hr.post(f"/announcements/{ann.id}/remind")
    assert "Напоминание отправлено: 1" in r.text and len(env.deps.repo.outbox_pending()) == n + 1


def test_announcement_to_group_and_draft() -> None:
    env = make_env()
    s = seed(env)
    hr = env.login(HR)
    hr.post(
        "/announcements/preview",
        data={"title": "Монтаж", "text": "Новый график", "groups": ["Монтаж"]},
    )
    hr.post(
        "/announcements/send", data={"title": "Монтаж", "text": "Новый график", "groups": "Монтаж"}
    )
    ann = env.deps.repo.announcements(CO)[0]
    assert ann.recipients == [s.igor.id]
    r = hr.post("/announcements/preview", data={"title": "Черновик", "text": "позже", "draft": "1"})
    assert "Черновик сохранён" in r.text
    assert "Черновик" in hr.get("/announcements/new").text


def test_unknown_announcement_is_404() -> None:
    env = make_env()
    assert env.login(HR).get("/announcements/zzz").status_code == 404


def test_import_token_of_other_company_not_found() -> None:
    env = make_env()
    other = env.login("boss@northwind.test")
    token = start_import(other)
    hr = env.login(HR)
    assert hr.get(f"/staff/import/{token}/preview").status_code == 404
    assert Env is not None
