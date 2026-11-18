from norma.textnorm import normalize


def test_yo_and_case() -> None:
    assert normalize("Ёлка, ПОДАЛА") == "елка, подала"


def test_quotes_and_dashes() -> None:
    assert normalize("«отпуск» — „28“ дней") == normalize('"отпуск" - "28" дней')


def test_nbsp_and_whitespace() -> None:
    assert normalize("28 календарных \n  дней") == "28 календарных дней"


def test_soft_hyphen_and_line_break_hyphen() -> None:
    assert normalize("работо­dатель") == "работодатель"
    assert normalize("работо-\nдатель") == "работодатель"


def test_footnote_markers_removed() -> None:
    assert normalize("отпуск<1> предоставляется[2]") == "отпуск предоставляется"
    assert normalize("отпуск*") == "отпуск"


def test_idempotent() -> None:
    s = "«Ёж» — тест<3>"
    assert normalize(normalize(s)) == normalize(s)
