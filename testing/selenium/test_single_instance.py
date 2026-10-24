"""TS-SEL-01  Selenium WebDriver - single browser instance, functional UI tests.

System-level black-box tests of the Angular front end against the live API.
Each test is one case of the test-case specification
(testing/docs/04-test-case-specification.md, section "Selenium").
"""
from __future__ import annotations

import time

import pytest
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC

from conftest import ACCOUNTS
from driver_factory import BASE_URL
from pages.login_page import LoginPage
from pages.shell_page import ShellPage

INVALID = "That email and password did not match an account"


def sign_in_student(driver) -> ShellPage:
    email, pw = ACCOUNTS["student"]
    LoginPage(driver).load().sign_in(email, pw)
    shell = ShellPage(driver)
    shell.wait_path_startswith("/student")
    return shell


def test_sel_001_login_page_renders_its_form(driver):
    page = LoginPage(driver).load()
    assert "REEP" in driver.title or driver.title
    assert driver.find_element(*LoginPage.ID).is_displayed()
    assert driver.find_element(*LoginPage.PASSWORD).get_attribute("type") == "password"
    assert driver.find_element(*LoginPage.SUBMIT).is_enabled()
    timing = page.navigation_timing_ms()
    assert timing.get("dom_content_loaded", 0) < 10_000, timing


def test_sel_002_empty_submit_shows_field_errors_and_stays_on_login(driver):
    page = LoginPage(driver).load()
    driver.find_element(*LoginPage.SUBMIT).click()
    time.sleep(0.5)
    assert page.path().startswith("/login")
    assert page.field_errors(), "an empty submit must say which field is missing"


def test_sel_003_wrong_password_shows_the_invalid_credentials_alert(driver):
    page = LoginPage(driver).load()
    page.sign_in(ACCOUNTS["student"][0], "definitely-wrong")
    assert INVALID in page.error_text()
    assert page.path().startswith("/login")


def test_sel_004_student_signs_in_and_lands_on_the_student_home(driver):
    shell = sign_in_student(driver)
    assert shell.student_home_outcome() == "data", "the home screen showed its error state"


def test_sel_005_every_student_sidebar_screen_opens_with_a_heading(driver):
    shell = sign_in_student(driver)
    targets = shell.nav_targets()
    assert len(targets) >= 5, targets
    broken = []
    for label, href in targets:
        driver.get(href)
        try:
            shell.wait.until(EC.visibility_of_element_located((By.TAG_NAME, "h1")))
        except Exception:
            broken.append((label, href))
        assert not ShellPage(driver).path().startswith("/login"), f"{label} signed the student out"
    assert not broken, f"screens with no heading: {broken}"


def test_sel_006_time_ledger_shows_the_day_and_its_hours(driver):
    shell = sign_in_student(driver)
    driver.get(f"{BASE_URL}/student/time-log")
    shell.h1()
    body = driver.find_element(By.TAG_NAME, "body").text
    assert "h" in body and any(w in body for w in ("Today", "today", "Submit", "Reconcile", "reconcile"))


def test_sel_007_sign_out_returns_to_login_and_the_back_button_does_not_reenter(driver):
    shell = sign_in_student(driver)
    shell.sign_out()
    shell.wait_path_startswith("/login")
    driver.back()
    time.sleep(1.5)
    assert ShellPage(driver).path().startswith("/login"), ShellPage(driver).path()


def test_sel_008_deep_link_without_a_session_redirects_to_login(driver):
    driver.get(f"{BASE_URL}/student/jobs")
    LoginPage(driver).wait_path_startswith("/login")


def test_sel_009_register_form_refuses_an_empty_submission_in_place(driver):
    driver.get(f"{BASE_URL}/register")
    page = LoginPage(driver)
    submit = page.wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "form button[type='submit']")))
    submit.click()
    alert = page.wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, "[role='alert']")))
    assert alert.text.strip()
    # The 2026-09-17 defect class: a form whose submit nobody owns navigates to "/register?".
    assert page.path().rstrip("?").startswith("/register") and "?" not in page.path()


def test_sel_010_main_admin_signs_in_through_the_admin_door(driver):
    page = LoginPage(driver).load().choose_admin_door()
    page.sign_in(*ACCOUNTS["admin"])
    page.wait_path_startswith("/admin")
    assert page.h1()


def test_sel_011_a_student_cannot_open_an_admin_screen(driver):
    sign_in_student(driver)
    driver.get(f"{BASE_URL}/admin/students")
    time.sleep(2)
    assert not ShellPage(driver).path().startswith("/admin"), ShellPage(driver).path()


@pytest.mark.parametrize("path", ["/login", "/register"])
def test_sel_012_phone_viewport_has_no_horizontal_scroll(phone, path):
    phone.get(f"{BASE_URL}{path}")
    page = LoginPage(phone)
    page.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "form")))
    assert page.horizontal_overflow_px() <= 1


def test_sel_013_phone_shell_uses_the_drawer(phone):
    email, pw = ACCOUNTS["student"]
    LoginPage(phone).load().sign_in(email, pw)
    shell = ShellPage(phone)
    shell.wait_path_startswith("/student")
    assert shell.hamburger_visible(), "below 900px the sidebar is a drawer behind a menu button"
    assert shell.horizontal_overflow_px() <= 1
