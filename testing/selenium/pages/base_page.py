"""Page Object Model base (Selenium's recommended pattern: tests speak in user
actions, locators live in one class per screen)."""
from __future__ import annotations

from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from driver_factory import BASE_URL


class BasePage:
    TIMEOUT = 20

    def __init__(self, driver: WebDriver):
        self.driver = driver
        self.wait = WebDriverWait(driver, self.TIMEOUT)

    def open(self, path: str) -> "BasePage":
        self.driver.get(f"{BASE_URL}{path}")
        return self

    def path(self) -> str:
        url = self.driver.current_url
        return url[len(BASE_URL):] if url.startswith(BASE_URL) else url

    def wait_path_startswith(self, prefix: str) -> None:
        self.wait.until(lambda d: self.path().startswith(prefix))

    def h1(self) -> str:
        return self.wait.until(EC.visibility_of_element_located((By.TAG_NAME, "h1"))).text.strip()

    def student_home_outcome(self) -> str:
        """Wait out the landing screen's LOADING state ('Landing' / 'Loading
        your overview...') and return which terminal state it reached: 'data'
        (h1 'Welcome back...') or 'error' ('Could not load your overview.').
        Reading the first visible h1 reads the loading placeholder (TW-007)."""
        def settled(d):
            try:
                for h in d.find_elements(By.TAG_NAME, "h1"):
                    if h.text.strip().startswith("Welcome back"):
                        return "data"
                if any("Could not load your overview" in e.text for e in d.find_elements(By.CSS_SELECTOR, ".dt-sub")):
                    return "error"
            except StaleElementReferenceException:  # the @switch swapped the node mid-read
                pass
            return False
        return self.wait.until(settled)

    def navigation_timing_ms(self) -> dict:
        """W3C Navigation Timing Level 2 for the last full page load."""
        return self.driver.execute_script("""
            const n = performance.getEntriesByType('navigation')[0];
            if (!n) return {};
            return {ttfb: Math.round(n.responseStart), dom_content_loaded: Math.round(n.domContentLoadedEventEnd),
                    load: Math.round(n.loadEventEnd), transfer_bytes: n.transferSize};
        """)

    def horizontal_overflow_px(self) -> int:
        return self.driver.execute_script(
            "return document.documentElement.scrollWidth - document.documentElement.clientWidth;")
