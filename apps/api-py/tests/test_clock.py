"""app/clock.py — the programme's calendar day, not the container's.

The container is UTC and the students are in India, so for five and a half
hours a night `date.today()` in a handler is yesterday. The ledger's two
date rules read this clock instead; these pin that it answers in the
configured zone and that a zone the host does not know degrades to UTC with a
warning rather than taking the ledger down.
"""

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app import clock
from app.config import settings


def test_local_today_is_the_programme_zones_day(monkeypatch):
    monkeypatch.setattr(settings, "programme_timezone", "Asia/Kolkata")
    clock._zone.cache_clear()
    assert str(clock.programme_tz()) == "Asia/Kolkata"
    expected = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    assert clock.local_today() == expected
    assert clock.local_now().tzinfo is not None


def test_a_zone_the_host_does_not_know_falls_back_to_utc_with_a_warning(monkeypatch, caplog):
    monkeypatch.setattr(settings, "programme_timezone", "Mars/Olympus_Mons")
    clock._zone.cache_clear()
    with caplog.at_level(logging.WARNING, logger="app.clock"):
        assert clock.programme_tz() is timezone.utc
    assert "Mars/Olympus_Mons" in caplog.text
    assert clock.local_today() == datetime.now(timezone.utc).date()
    clock._zone.cache_clear()


def test_a_blank_setting_means_utc(monkeypatch):
    monkeypatch.setattr(settings, "programme_timezone", "  ")
    clock._zone.cache_clear()
    assert clock.programme_tz() is ZoneInfo("UTC") or str(clock.programme_tz()) == "UTC"
    clock._zone.cache_clear()


def test_the_default_zone_is_india():
    from app.config import Settings

    assert Settings.model_fields["programme_timezone"].default == "Asia/Kolkata"
    assert Settings.model_fields["ledger_edit_window_days"].default == 2
