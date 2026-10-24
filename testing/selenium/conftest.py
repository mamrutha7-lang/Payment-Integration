"""pytest wiring for the single-instance Selenium suite.

One browser per test (function scope) so a failing case cannot leak its
session into the next; a screenshot is saved for every failure.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from driver_factory import make_driver  # noqa: E402

SHOTS = Path(__file__).resolve().parents[1] / "results" / "selenium" / "screenshots"


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    if rep.when == "call" and rep.failed and "driver" in item.fixturenames:
        SHOTS.mkdir(parents=True, exist_ok=True)
        item.funcargs["driver"].save_screenshot(str(SHOTS / f"{item.name}.png"))


@pytest.fixture
def driver():
    d = make_driver()
    yield d
    d.quit()


@pytest.fixture
def phone():
    d = make_driver(width=390, height=844, mobile=True)
    yield d
    d.quit()


ACCOUNTS = {
    # A RESERVED load-test student, never the seeded one: the seeded student is
    # the Playwright e2e suite's, and REEP keeps one session per account.
    "student": ("loadtest103@bgscet.ac.in", "LoadTest#2026"),
    "admin": ("admin@bgscet.ac.in", "admin123"),
    "mentor": ("mentor@bgscet.ac.in", "mentor123"),
}
os.environ.setdefault("CHROMEDRIVER", "/opt/chromedriver141/chromedriver-linux64/chromedriver"
                      if Path("/opt/chromedriver141/chromedriver-linux64/chromedriver").exists() else "")
if not os.environ["CHROMEDRIVER"]:
    del os.environ["CHROMEDRIVER"]
