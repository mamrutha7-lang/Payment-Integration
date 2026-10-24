"""One place that knows how to start a browser for the REEP Selenium suites.

Local by default (headless Chromium + a ChromeDriver of the SAME major
version - a mismatch is "session not created", not a test failure). Set
SELENIUM_REMOTE_URL (e.g. http://localhost:4444) to run on a Selenium Grid
instead; see testing/selenium/grid/docker-compose.yml.

    CHROME_BIN          browser binary   (default: Playwright's Chromium in /opt/pw-browsers)
    CHROMEDRIVER        driver binary    (default: Selenium Manager resolves one)
    HEADLESS            1 (default) / 0
    LIGHT_CHROME        1 = fewer processes per browser (for 100 browsers on one host)
"""
from __future__ import annotations

import glob
import os

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

BASE_URL = os.environ.get("REEP_WEB", "http://localhost:4200").rstrip("/")


def _default_chrome() -> str | None:
    hits = sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome"))
    return hits[-1] if hits else None


def make_driver(width: int = 1366, height: int = 900, mobile: bool = False) -> webdriver.Remote:
    opts = Options()
    if os.environ.get("HEADLESS", "1") != "0":
        opts.add_argument("--headless=new")
    for arg in ("--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                "--disable-extensions", "--no-first-run", f"--window-size={width},{height}"):
        opts.add_argument(arg)
    if mobile:
        opts.add_experimental_option("mobileEmulation", {
            "deviceMetrics": {"width": width, "height": height, "pixelRatio": 3.0},
            "userAgent": "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/141.0 Mobile Safari/537.36"})
    if os.environ.get("LIGHT_CHROME") == "1":
        # For many browsers on one host: one renderer, no per-site process
        # isolation. ~800 Chrome processes / 15 GB for 100 default browsers
        # did not fit a 16 GB test bed; this is what made 100 simultaneous fit.
        for arg in ("--disable-features=site-per-process,IsolateOrigins,Translate,OptimizationHints",
                    "--renderer-process-limit=1", "--disable-background-networking",
                    "--disable-component-update", "--js-flags=--max-old-space-size=256"):
            opts.add_argument(arg)
    opts.set_capability("goog:loggingPrefs", {"browser": "SEVERE"})

    remote = os.environ.get("SELENIUM_REMOTE_URL")
    if remote:
        return webdriver.Remote(command_executor=remote, options=opts)
    binary = os.environ.get("CHROME_BIN") or _default_chrome()
    if binary:
        opts.binary_location = binary
    driver_path = os.environ.get("CHROMEDRIVER")
    service = Service(executable_path=driver_path) if driver_path else Service()
    driver = webdriver.Chrome(options=opts, service=service)
    driver.set_page_load_timeout(60)
    return driver
