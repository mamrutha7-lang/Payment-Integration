"""The production boot guard: what this process REFUSES to start with.

`Settings.production_boot_failures()` (app/config.py) is the highest-severity
finding of the 2026-08 backend audit, made enforceable. A production host running
on the AUTH_SECRET committed to this repository is not a weak default to rotate
later: it is one forged `{"role":"DIRECTOR"}` cookie away from every student's
marks, attendance and USN — no login, no Google round trip, no database row
involved. app/main.py's lifespan asks the question and raises, so the process
never binds a port.

It shipped with no test, and that is a bad shape for a guard of this kind: it can
only fire on a host nobody runs the suite on. Nothing would have noticed it being
softened to a warning, re-keyed onto the wrong flag, or quietly returning [].

THE MOST IMPORTANT SECTION HERE IS THE LAST ONE. A boot guard that trips on a
laptop or in CI does not get fixed — it gets deleted by whoever is trying to ship
something else that afternoon. So "empty on every development ENV" is pinned as
hard as "non-empty in production", and the real `settings` singleton the suite
itself boots under is asserted directly.

NOTHING HERE MUTATES THE `settings` SINGLETON. Every case builds its own
`Settings(...)`, and the two lifespan tests rebind `app.main.settings` through
monkeypatch, which reverts at teardown. Mutating the shared object would leak
`env="prod"` into whatever module ran next — and the properties that read it
decide cookie `Secure`, password sign-in and voice-worker auth, which is three
other test modules' subject matter.

`_env_file=None` on every construction, so a developer's own apps/api-py/.env
cannot decide whether these pass. Without it, "a clean production config boots"
would quietly be testing whatever secret happens to be on this machine.

The private names are imported deliberately. The guard compares against these
exact constants, so the test must too: a copy pasted here would keep passing on
the day someone edits a default and leaves the guard testing a string nobody uses
any more — which is the failure config.py's own comment says the shared constants
exist to prevent.
"""

import asyncio
import logging

import pytest

from app import main
from app.config import (
    AUTH_SECRET_MIN_CHARS,
    Settings,
    _DEV_AUTH_SECRET,
    _DEV_DATABASE_URL,
    _DEV_DB_PASSWORD,
    _DEV_ENV_NAMES,
    _PLACEHOLDER_SECRET_MARKERS,
    _PROD_ENV_NAMES,
)
from app.config import settings as live_settings

# A secret that is none of the things the guard objects to: not blank, not the
# committed default, no placeholder marker, comfortably over the floor.
GOOD_SECRET = "b7d1" + "9e3a" * 10
# ...and a database URL that does not carry the published dev password.
GOOD_DATABASE_URL = "postgresql+psycopg://reep_app:not-the-repo-password@db.internal:5432/reep"


def _cfg(**overrides) -> Settings:
    """A production Settings that is CLEAN except for what the caller names.

    Every test starts from a config that boots and then breaks exactly one thing,
    so a failure names its cause instead of the pile.
    """
    values = {
        "env": "prod",
        "auth_secret": GOOD_SECRET,
        "database_url": GOOD_DATABASE_URL,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _joined(problems: list[str]) -> str:
    return "\n".join(problems)


# ---------------------------------------------------------------------------
# It fires — once per distinct failure, on a host that says it is production
# ---------------------------------------------------------------------------
def test_the_committed_auth_secret_is_refused():
    """The finding itself: the signing key printed in .env.example, in production.

    This is the one that matters. Everything else in this file exists to protect
    it.
    """
    problems = _cfg(auth_secret=_DEV_AUTH_SECRET).production_boot_failures()

    assert len(problems) == 1, problems
    assert "AUTH_SECRET" in problems[0]
    # It has to say what to DO. An operator meeting this at 2am needs the command.
    assert "secrets.token_hex" in problems[0]


@pytest.mark.parametrize("blank", ["", "   ", "\t\n "])
def test_a_blank_auth_secret_is_refused(blank):
    """Whitespace is blank: `AUTH_SECRET=` in a template, or `AUTH_SECRET=" "`.

    Unhandled, this is the worst case in the file rather than the mildest. HS256
    signs perfectly happily with an empty key, so every session token in the
    deployment would be forgeable by anyone who guessed the field was empty —
    and there would be no symptom at all until someone did.
    """
    problems = _cfg(auth_secret=blank).production_boot_failures()

    assert len(problems) == 1, problems
    assert "blank" in problems[0]
    assert "secrets.token_hex" in problems[0]


@pytest.mark.parametrize("marker", _PLACEHOLDER_SECRET_MARKERS)
def test_a_placeholder_secret_is_refused_however_it_is_padded(marker):
    """Edited is not replaced.

    The equality test against the committed default is defeated by changing two
    characters, and a deploy template that still says `change-me` has been read
    by everyone who ever cloned this repo. Padded past the length floor on
    purpose, so this proves the PLACEHOLDER branch fired and not the short-key
    one — they give different advice, and a reader has to be able to tell which
    they hit.
    """
    secret = f"{marker}-{'0' * AUTH_SECRET_MIN_CHARS}"
    assert len(secret) >= AUTH_SECRET_MIN_CHARS

    problems = _cfg(auth_secret=secret).production_boot_failures()

    assert len(problems) == 1, problems
    assert "development value" in problems[0]


def test_a_placeholder_marker_is_matched_case_insensitively():
    """Deploy templates SHOUT: `AUTH_SECRET=CHANGE-ME-BEFORE-PRODUCTION`."""
    problems = _cfg(
        auth_secret="CHANGE-ME-BEFORE-PRODUCTION-PLEASE-0123456"
    ).production_boot_failures()

    assert len(problems) == 1, problems
    assert "development value" in problems[0]


def test_a_secret_under_the_floor_is_refused():
    problems = _cfg(auth_secret="a1b2c3" * 5).production_boot_failures()  # 30 chars

    assert len(problems) == 1, problems
    assert str(AUTH_SECRET_MIN_CHARS) in problems[0]
    # The length is named because it is the number the operator has to beat.
    assert "30 characters" in problems[0]


def test_the_length_floor_counts_the_stripped_secret():
    """A short key padded with spaces is a short key.

    `AUTH_SECRET="short  "` out of a shell script is easy to produce by accident.
    This guard and app/security.py must agree on what the key IS, and security.py
    signs with the raw string — so the strict reading (count the real characters)
    is the safe one in the only direction that matters.
    """
    padded = " " * 40 + "a1b2c3" * 5 + " " * 40
    assert len(padded) > AUTH_SECRET_MIN_CHARS

    problems = _cfg(auth_secret=padded).production_boot_failures()

    assert len(problems) == 1, problems
    assert "30 characters" in problems[0]


def test_a_real_secret_containing_the_word_secret_is_allowed():
    """The marker list is deliberately narrow, and this is why.

    config.py says it in as many words: a genuine random secret can contain
    "secret", so that word is NOT a marker. Widening the list to catch it would
    refuse a legitimate deployment at boot — the one failure mode a guard with no
    override flag cannot afford.
    """
    assert _cfg(auth_secret="secret-" + "f3a9" * 12).production_boot_failures() == []


def test_the_committed_database_url_is_refused():
    problems = _cfg(database_url=_DEV_DATABASE_URL).production_boot_failures()

    assert len(problems) == 1, problems
    assert "DATABASE_URL" in problems[0]
    assert "ALTER ROLE" in problems[0]


def test_a_real_host_carrying_the_published_password_is_refused():
    """It is the PASSWORD that is checked, not the whole default URL.

    The dangerous case is not a production box pointed at localhost:5433 — that
    one is diagnosed within a minute, because every dashboard is empty. It is a
    real production host whose `reep` role still carries the password printed in
    .env.example: it works perfectly, and it is readable by anyone who cloned us.
    """
    url = f"postgresql+psycopg://reep:{_DEV_DB_PASSWORD}@db.internal:5432/reep_prod"
    problems = _cfg(database_url=url).production_boot_failures()

    assert len(problems) == 1, problems
    assert "DATABASE_URL" in problems[0]


def test_every_problem_is_reported_in_one_pass():
    """Two faults, two sentences, one boot.

    Returning at the first problem would mean an operator fixes one thing,
    redeploys, waits, and meets the next — which is how a guard earns the
    reputation that gets it deleted.
    """
    problems = _cfg(
        auth_secret=_DEV_AUTH_SECRET, database_url=_DEV_DATABASE_URL
    ).production_boot_failures()

    assert len(problems) == 2, problems
    assert any("AUTH_SECRET" in p for p in problems)
    assert any("DATABASE_URL" in p for p in problems)


@pytest.mark.parametrize("env", sorted(_PROD_ENV_NAMES))
def test_it_fires_for_every_spelling_of_production(env):
    assert _cfg(env=env, auth_secret=_DEV_AUTH_SECRET).production_boot_failures()


@pytest.mark.parametrize("env", ["  PROD  ", "Production", "LIVE"])
def test_the_env_name_is_read_case_and_whitespace_insensitively(env):
    """`ENV=Production` typed into a deploy UI must not be a way past this."""
    assert _cfg(env=env, auth_secret=_DEV_AUTH_SECRET).production_boot_failures()


# ---------------------------------------------------------------------------
# It does not fire on a configuration that is actually fine
# ---------------------------------------------------------------------------
def test_a_clean_production_config_boots():
    assert _cfg().production_boot_failures() == []


def test_a_secret_exactly_on_the_floor_is_accepted():
    """A floor is a floor. Off by one here refuses a compliant deployment."""
    assert _cfg(auth_secret="c" * AUTH_SECRET_MIN_CHARS).production_boot_failures() == []


# ---------------------------------------------------------------------------
# What the failure text may and may not contain
# ---------------------------------------------------------------------------
def test_no_message_ever_repeats_the_credential():
    """These sentences land in deploy logs, container output and aggregators.

    Which is precisely where a signing key or a database password must not be
    copied to. A guard that leaks the credential while complaining about it has
    made the situation worse, in a place that retains it.
    """
    secret = "unmistakable-secret-value-" + "7" * 40
    url = f"postgresql+psycopg://reep:{_DEV_DB_PASSWORD}@db.internal:5432/reep_prod"
    text = _joined(_cfg(auth_secret=secret, database_url=url).production_boot_failures())

    assert secret not in text
    assert _DEV_DB_PASSWORD not in text
    assert url not in text
    assert "db.internal" not in text


def test_the_dev_default_secret_is_not_echoed_back_either():
    text = _joined(_cfg(auth_secret=_DEV_AUTH_SECRET).production_boot_failures())

    assert _DEV_AUTH_SECRET not in text
    # It names the FILE the value came from instead, which is enough to act on.
    assert ".env.example" in text


# ---------------------------------------------------------------------------
# app/main.py: the half that actually refuses
# ---------------------------------------------------------------------------
def _boot(monkeypatch, cfg: Settings) -> None:
    """Run app/main.py's lifespan against `cfg`, start to finish.

    Rebinds the name `settings` INSIDE app.main only — the singleton every other
    module holds is untouched, which is what keeps this file from deciding
    whether test_auth_rbac or test_voice_gates passes.

    Exiting the context is safe: the teardown only asks live interview sessions
    to stop, and there are none in this process.
    """
    monkeypatch.setattr(main, "settings", cfg)

    async def _run() -> None:
        async with main.lifespan(main.app):
            pass

    asyncio.run(_run())


def test_the_lifespan_refuses_to_start_when_the_guard_is_non_empty(monkeypatch, caplog):
    """Deciding and refusing are two halves, and only one of them existed.

    `production_boot_failures()` returning a list is worth nothing if its caller
    logs it and serves anyway. Uvicorn turns this exception into "Application
    startup failed. Exiting." — the port is never bound, which is the whole
    control.
    """
    with caplog.at_level(logging.CRITICAL, logger="reep.startup"):
        with pytest.raises(RuntimeError) as raised:
            _boot(
                monkeypatch,
                _cfg(auth_secret=_DEV_AUTH_SECRET, database_url=_DEV_DATABASE_URL),
            )

    message = str(raised.value)
    assert "REFUSED to start" in message
    assert "AUTH_SECRET" in message and "DATABASE_URL" in message
    # Raised AND logged, deliberately: the traceback is complete but easy to lose
    # in a container log, and the CRITICAL line is the one an aggregator keeps.
    critical = [r for r in caplog.records if r.levelno >= logging.CRITICAL]
    assert critical, "the refusal was raised but never logged at CRITICAL"
    assert "REFUSED to start" in critical[0].getMessage()
    # Not even here. See test_no_message_ever_repeats_the_credential.
    assert _DEV_AUTH_SECRET not in message
    assert _DEV_DB_PASSWORD not in message


def test_the_lifespan_starts_on_a_clean_production_config(monkeypatch):
    """The guard must let a correct production deployment through.

    A guard that refuses everything is discovered on the day of the deploy and
    removed that same evening.
    """
    _boot(monkeypatch, _cfg())


# ---------------------------------------------------------------------------
# ...and it must never, ever fire on a development machine or in CI
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("env", sorted(_DEV_ENV_NAMES))
def test_it_never_fires_on_a_development_env(env):
    """The worst config imaginable, on every ENV that is allowed to hold it.

    Parametrized over the allowlist itself rather than a copy, so a name added
    there is covered here the moment it is added. These are the machines where
    the committed secret and the dev database are the CORRECT values: a laptop, a
    CI runner, this very test run. Refusing here would make the guard something
    to be removed rather than something to be satisfied.
    """
    cfg = _cfg(env=env, auth_secret=_DEV_AUTH_SECRET, database_url=_DEV_DATABASE_URL)
    assert cfg.production_boot_failures() == []


def test_it_does_not_fire_on_an_unrecognised_env():
    """`staging` boots. That is the current, deliberate scope of THIS guard.

    Written down so nobody has to guess: production_boot_failures() is keyed on
    `is_prod`, a match against the four literal production spellings, so a
    staging or uat box holding the committed secret starts anyway. The audit's
    fail-closed treatment of an unrecognised ENV lives in the guards that can
    afford it — `insecure_cookies_allowed`, `worker_auth_optional`,
    `password_login_allowed` — because those degrade one feature, where this one
    refuses to boot at all and would take a demo box down over a name it did not
    recognise.

    If that trade is ever revisited, this test is the thing to change on purpose
    rather than the thing that mysteriously starts failing.
    """
    assert _cfg(env="staging", auth_secret=_DEV_AUTH_SECRET).production_boot_failures() == []


def test_the_suite_itself_boots_clean():
    """The live singleton, exactly as every other test module reads it.

    conftest.py's `client` fixture boots the real app through TestClient, which
    runs the real lifespan. If this ever returns problems, the whole DB-backed
    suite fails at fixture setup with a RuntimeError out of app/main.py — and
    this one assertion is what says why.
    """
    assert live_settings.production_boot_failures() == [], (
        "the boot guard fires under the suite's own ENV="
        f"{live_settings.env!r}. Every test that uses the `client` fixture is "
        "about to fail at startup."
    )
