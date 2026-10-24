"""The programme's calendar day.

`date.today()` answers in the PROCESS's timezone, and on Fargate that is UTC:
the image sets no TZ and nothing in the task definition does either. Every
student is in India, five and a half hours ahead. So for the whole of
00:00–05:30 IST the process still thinks it is yesterday, and a rule written
as `body.day > date.today()` refuses a student who opens the Time Allocation
Ledger after midnight to fill in the day that has just ended — "You cannot log
a day that has not happened yet", about a day that has.

A lock that closes a day N days after it has the same edge, moved: the day
would stay open five and a half hours longer than the sentence on the screen
says. So the two rules that decide whether a day is open or shut read THIS
clock, never the process's, and the client anchors its stepper on the `today`
the server hands back rather than on the handset's own date.

`PROGRAMME_TIMEZONE` is a setting because the deployment is one college in one
country today and a college abroad is a `.env` line, not a code change. An
unknown zone name falls back to UTC with a warning rather than refusing to
boot: the wrong `today` for a few hours a night is a bug somebody can read in
the log; a ledger that answers 500 to every student is an outage.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone, tzinfo
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import settings

log = logging.getLogger(__name__)


@lru_cache(maxsize=8)
def _zone(name: str) -> tzinfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        log.warning(
            "PROGRAMME_TIMEZONE=%r is not a zone this host knows; using UTC. "
            "The ledger's 'today' will be wrong for part of every night.",
            name,
        )
        return timezone.utc


def programme_tz() -> tzinfo:
    """The zone the programme's calendar runs in (default Asia/Kolkata)."""
    return _zone(settings.programme_timezone.strip() or "UTC")


def local_now() -> datetime:
    """Now, as an aware datetime in the programme's zone."""
    return datetime.now(programme_tz())


def local_today() -> date:
    """The calendar day it is for the students, not for the container."""
    return local_now().date()
