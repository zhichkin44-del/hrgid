"""П0: вход по почте и коду, блок после 5 ошибок, сессии, журнал входов."""

from __future__ import annotations

from .conftest import ADMIN, CO, HR, Env


def test_unauthenticated_goes_to_login(env: Env) -> None:
    c = env.anon()
    r = c.get("/queue")
    assert r.status_code == 200 and str(r.url).endswith("/login")
    assert "Вход для HR" in r.text


def test_login_flow_and_today(env: Env) -> None:
    c = env.login(HR)
    r = c.get("/")
    assert r.status_code == 200 and "Анна Воронова" in r.text
    # код не светится в письме-логе приложения, но доходит получателю
    assert env.mailer.sent[-1][0] == HR


def test_email_not_in_hr_list_has_no_access(env: Env) -> None:
    c = env.anon()
    r = c.post("/login", data={"email": "stranger@x.ru"})
    assert "Нет доступа" in r.text and not env.mailer.sent


def test_bad_email_format(env: Env) -> None:
    r = env.anon().post("/login", data={"email": "не почта"})
    assert "Проверьте почту" in r.text


def test_wrong_code_counts_attempts_then_blocks_15_minutes(env: Env) -> None:
    c = env.anon()
    c.post("/login", data={"email": HR})
    for left in (4, 3, 2, 1):
        r = c.post("/login/code", data={"code": "000000"})
        assert "Код неверный" in r.text and f"Осталось {left} из 5" in r.text
    r = c.post("/login/code", data={"code": "000000"})
    assert "закрыт на 15 минут" in r.text
    # даже верный код в блоке не пускает
    good = env.mailer.last_code(HR)
    r = c.post("/login/code", data={"code": good})
    assert "закрыт на 15 минут" in r.text and "hrgid_sid" not in c.cookies
    env.clock.advance(minutes=16)
    c.post("/login/resend")
    r = c.post("/login/code", data={"code": env.mailer.last_code(HR)})
    assert "hrgid_sid" in c.cookies
    log = env.deps.auth.login_log(CO)
    assert [x["result"] for x in log if x["result"] in ("blocked", "bad_code")].count("bad_code") == 4


def test_code_expires_in_10_minutes(env: Env) -> None:
    c = env.anon()
    c.post("/login", data={"email": HR})
    code = env.mailer.last_code(HR)
    env.clock.advance(minutes=11)
    r = c.post("/login/code", data={"code": code})
    assert "Код просрочен" in r.text and "hrgid_sid" not in c.cookies


def test_code_is_single_use(env: Env) -> None:
    c = env.anon()
    c.post("/login", data={"email": HR})
    code = env.mailer.last_code(HR)
    c.post("/login/code", data={"code": code})
    assert "hrgid_sid" in c.cookies
    c2 = env.anon()
    c2.post("/login", data={"email": HR})
    r = c2.post("/login/code", data={"code": code})  # тот же код второй раз
    assert "hrgid_sid" not in c2.cookies, r.text[:80]


def test_resend_only_after_60_seconds(env: Env) -> None:
    c = env.anon()
    c.post("/login", data={"email": HR})
    n = len(env.mailer.sent)
    r = c.post("/login/resend")
    assert "через" in r.text and len(env.mailer.sent) == n
    env.clock.advance(seconds=61)
    c.post("/login/resend")
    assert len(env.mailer.sent) == n + 1


def test_session_expires_after_8_hours_idle(env: Env) -> None:
    c = env.login(HR)
    env.clock.advance(hours=7)
    assert c.get("/").status_code == 200  # действие продлевает сессию
    env.clock.advance(hours=9)
    r = c.get("/queue")
    assert "Сессия закрыта" in r.text
    log = [x["result"] for x in env.deps.auth.login_log(CO)]
    assert "session_expired" in log


def test_logout_asks_then_closes_and_logs(env: Env) -> None:
    c = env.login(HR)
    r = c.get("/logout")
    assert "Выйти из панели?" in r.text
    r = c.post("/logout")
    assert str(r.url).endswith("/login")
    assert c.get("/queue").url.path == "/login"
    assert "logout" in [x["result"] for x in env.deps.auth.login_log(CO)]


def test_tokens_and_codes_stored_hashed(env: Env) -> None:
    c = env.login(HR)
    token = c.cookies["hrgid_sid"]
    blob = str(env.store._docs)
    assert token not in blob
    assert env.mailer.last_code(HR) not in blob


def test_foreign_origin_post_rejected(env: Env) -> None:
    c = env.login(HR)
    r = c.post("/kb", data={"question": "q"}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403


def test_admin_can_login_too(env: Env) -> None:
    c = env.login(ADMIN)
    assert "Администратор" in c.get("/").text


def test_login_log_tracks_codes(env: Env) -> None:
    env.login(HR)
    results = [x["result"] for x in env.deps.auth.login_log(CO)]
    assert results[:2] == ["ok", "code_sent"]
