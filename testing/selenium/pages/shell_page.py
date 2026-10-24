"""The signed-in shell: sidebar navigation, account menu, sign-out."""
from __future__ import annotations

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC

from .base_page import BasePage


class ShellPage(BasePage):
    NAV_LINKS = (By.CSS_SELECTOR, "nav a[href]")
    # A student's app bar has one Sign out button; staff open the account menu first.
    DIRECT_SIGN_OUT = (By.CSS_SELECTOR, "header.appbar button.appbar__signout")
    ACCOUNT_BUTTON = (By.CSS_SELECTOR, "header.appbar button.account__button")
    SIGN_OUT = (By.XPATH, "//*[@role='menuitem'][normalize-space(.)='Sign out']")
    HAMBURGER = (By.CSS_SELECTOR, "header.appbar button.appbar__nav-toggle")

    def nav_targets(self) -> list[tuple[str, str]]:
        self.wait.until(EC.presence_of_element_located(self.NAV_LINKS))
        seen, out = set(), []
        for a in self.driver.find_elements(*self.NAV_LINKS):
            href = a.get_attribute("href") or ""
            label = " ".join((a.get_attribute("textContent") or "").split())
            if href and href not in seen and "/api/" not in href:
                seen.add(href)
                out.append((label, href))
        return out

    def sign_out(self) -> None:
        direct = [b for b in self.driver.find_elements(*self.DIRECT_SIGN_OUT) if b.is_displayed()]
        if direct:
            direct[0].click()
            return
        self.wait.until(EC.element_to_be_clickable(self.ACCOUNT_BUTTON)).click()
        self.wait.until(EC.element_to_be_clickable(self.SIGN_OUT)).click()

    def hamburger_visible(self) -> bool:
        return any(b.is_displayed() for b in self.driver.find_elements(*self.HAMBURGER))

    def severe_console_errors(self) -> list[str]:
        try:
            return [e["message"] for e in self.driver.get_log("browser") if e.get("level") == "SEVERE"]
        except Exception:  # remote drivers may not expose logs
            return []
