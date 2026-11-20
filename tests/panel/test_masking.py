"""Маскирование ПДн в логах и трассах панели (SPEC-09 §9): 30 образцов, порог ≥ 29 из 30."""

from __future__ import annotations

import logging

import pytest

from hrgid.panel.maskinglog import install, mask_text, mask_trace

pytestmark = pytest.mark.natasha  # ФИО ищет natasha; здесь модели грузятся, в CI — всегда

# (текст, то, чего в результате быть не должно)
SAMPLES: list[tuple[str, list[str]]] = [
    # ФИО в разных падежах и форматах (natasha)
    ("Сотрудник Иванов Иван Иванович просит отпуск", ["Иванов", "Иван Иванович"]),
    ("Ответ для Петровой Марии Сергеевны отправлен", ["Петровой", "Марии"]),
    ("Обращение от Сидорова Петра", ["Сидорова"]),
    ("Передать вопрос Козловой Анне Викторовне", ["Козловой", "Анне"]),
    ("Смирнова Ольга спрашивает про больничный", ["Смирнова Ольга"]),
    ("У Лебедева Дмитрия Олеговича нет заработка за год", ["Лебедева", "Дмитрия"]),
    ("Игорь Петров хочет перенести отпуск", ["Игорь Петров"]),
    ("Добрый день, меня зовут Анастасия Новикова", ["Новикова"]),
    ("Заявление Кузнецова Алексея Павловича от 12.11", ["Кузнецова", "Алексея"]),
    ("Справка выдана Морозовой Екатерине", ["Морозовой"]),
    # паспорт
    ("Паспорт 4512 345678 выдан в 2015", ["4512 345678"]),
    ("серия 4512 № 345678", ["345678"]),
    ("паспорт 45 12 345678", ["345678"]),
    ("Данные: 4509123456", ["4509123456"]),
    # СНИЛС
    ("СНИЛС 123-456-789 01", ["123-456-789"]),
    ("снилс сотрудника 112-233-445 95 в заявлении", ["112-233-445"]),
    ("Номер СНИЛС: 045-678-901-23", ["045-678-901"]),
    # ИНН
    ("ИНН 770812345678 физлица", ["770812345678"]),
    ("инн: 7708123456 компании", ["7708123456"]),
    ("Налогоплательщик 500100732259", ["500100732259"]),
    # телефон
    ("Позвоните на +7 (916) 123-45-67", ["123-45-67"]),
    ("Телефон 8 916 123 45 67", ["123 45 67"]),
    ("тел. +79161234567", ["9161234567"]),
    ("Мобильный 8(916)123-45-67 после обеда", ["123-45-67"]),
    # почта
    ("Пишите на ivan.petrov@example.com", ["ivan.petrov@example.com"]),
    ("Почта: o.smirnova@studio.ru", ["o.smirnova@studio.ru"]),
    ("hr+test@zhichkin.test просит доступ", ["hr+test@zhichkin.test"]),
    # смешанные
    (
        "Иванов Иван Иванович, паспорт 4512 345678, тел. +7 916 123-45-67",
        ["Иванов", "345678", "123-45-67"],
    ),
    (
        "Петрова Мария, СНИЛС 123-456-789 01, почта maria@x.ru",
        ["Петрова", "123-456-789", "maria@x.ru"],
    ),
    ("ИНН 770812345678 и телефон 89161234567 у Сидорова Петра", ["770812345678", "9161234567", "Сидорова"]),
]


def test_thirty_samples_masked_at_least_29() -> None:
    assert len(SAMPLES) == 30
    failed = []
    for text, forbidden in SAMPLES:
        out = mask_text(text)
        if any(f in out for f in forbidden):
            failed.append((text, out))
    assert len(failed) <= 1, failed
    print(f"маскирование: {30 - len(failed)} из 30")


def test_labels_replace_values_and_keep_context() -> None:
    out = mask_text("Иванов Иван просит отпуск, тел. +7 916 123-45-67")
    assert "[ФИО_1]" in out and "[ТЕЛЕФОН_1]" in out and "просит отпуск" in out


@pytest.fixture()
def masked_logging() -> None:
    install()


def test_logger_masks_message_and_args(masked_logging: None, caplog: pytest.LogCaptureFixture) -> None:
    log = logging.getLogger("hrgid.panel.test")
    with caplog.at_level(logging.INFO, logger="hrgid"):
        log.info("вход %s с паспортом %s", "o.smirnova@studio.ru", "4512 345678")
        log.error("сотрудник Иванов Иван Иванович написал на 8 916 123 45 67")
    text = "\n".join(r.getMessage() for r in caplog.records)
    for secret in ("o.smirnova@studio.ru", "345678", "Иванов", "123 45 67"):
        assert secret not in text, text


def test_exception_text_never_logged_only_type(
    masked_logging: None, caplog: pytest.LogCaptureFixture
) -> None:
    from .conftest import HR, make_env

    env = make_env()
    c = env.login(HR)

    def boom(*_: object, **__: object) -> list[object]:
        raise RuntimeError("Иванов Иван Иванович, СНИЛС 123-456-789 01")

    env.deps.repo.employees = boom  # type: ignore[method-assign,assignment]
    with caplog.at_level(logging.ERROR, logger="hrgid"):
        r = c.get("/staff")
    assert r.status_code == 500 and "Повторить" in r.text
    text = "\n".join(rec.getMessage() for rec in caplog.records) + r.text
    assert "Иванов" not in text and "123-456-789" not in text and "RuntimeError" in text


def test_trace_fields_masked() -> None:
    out = mask_trace({"q": "Иванов Иван, ИНН 770812345678", "ms": 12})
    assert "Иванов" not in out["q"] and "770812345678" not in out["q"] and out["ms"] == 12


def test_login_code_and_email_not_in_app_log(caplog: pytest.LogCaptureFixture) -> None:
    from .conftest import HR, make_env

    install()
    env = make_env()
    with caplog.at_level(logging.DEBUG):
        env.login(HR)
    code = env.mailer.last_code(HR)
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert code not in text and HR not in text


def test_journal_and_traces_in_panel_carry_no_correspondence() -> None:
    from .conftest import CO, HR, make_env
    from .seed import seed

    env = make_env()
    s = seed(env)
    env.login(HR).post(f"/queue/{s.t1.key}/answer", data={"text": "СНИЛС 123-456-789 01 в ответе"})
    dump = str([e.__dict__ for e in env.deps.journal.entries(CO)])
    assert "123-456-789" not in dump and "перенести отпуск" not in dump
