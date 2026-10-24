from __future__ import annotations

from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC

from .base_page import BasePage


class LoginPage(BasePage):
    ID = (By.CSS_SELECTOR, "input[name='id']")
    PASSWORD = (By.CSS_SELECTOR, "input[name='password']")
    SUBMIT = (By.CSS_SELECTOR, "form button.btn-primary[type='submit']")
    ERROR = (By.CSS_SELECTOR, ".alert--error[role='alert']")
    FIELD_ERROR = (By.CSS_SELECTOR, ".field__err[role='alert']")
    ADMIN_DOOR = (By.XPATH, "//button[contains(normalize-space(.), 'Main Admin') or contains(@aria-label, 'Main Admin')]")
    FACULTY = (By.XPATH, "//*[@role='radio'][starts-with(normalize-space(.), 'Faculty')]")

    def load(self) -> "LoginPage":
        self.open("/login")
        self.wait.until(EC.element_to_be_clickable(self.ID))
        return self

    def choose_admin_door(self) -> "LoginPage":
        self.wait.until(EC.element_to_be_clickable(self.ADMIN_DOOR)).click()
        self.wait.until(EC.element_to_be_clickable(self.ID))
        return self

    def choose_faculty(self) -> "LoginPage":
        self.wait.until(EC.element_to_be_clickable(self.FACULTY)).click()
        return self

    def sign_in(self, email: str, password: str) -> None:
        # Angular can re-render the form between find and type (seen once in
        # 100 concurrent browsers: StaleElementReferenceException). Re-find
        # and retry rather than fail the journey on a DOM swap.
        for attempt in range(3):
            try:
                return self._sign_in(email, password)
            except StaleElementReferenceException:
                if attempt == 2:
                    raise

    def _sign_in(self, email: str, password: str) -> None:
        field = self.driver.find_element(*self.ID)
        field.clear()
        field.send_keys(email)
        pw = self.driver.find_element(*self.PASSWORD)
        pw.clear()
        pw.send_keys(password)
        self.driver.find_element(*self.SUBMIT).click()

    def error_text(self) -> str:
        return self.wait.until(EC.visibility_of_element_located(self.ERROR)).text.strip()

    def field_errors(self) -> list[str]:
        return [e.text.strip() for e in self.driver.find_elements(*self.FIELD_ERROR) if e.is_displayed()]
