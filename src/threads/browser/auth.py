import json
import os
import time

from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

COOKIES_FILE = os.environ.get("THREADS_COOKIES_PATH", "data/threads_cookies.json")
SCREENSHOTS_DIR = os.environ.get("THREADS_SCREENSHOTS_PATH", "data/screenshots")


def _set_input_value(driver, element, value):
    actions = ActionChains(driver)
    actions.move_to_element(element).click().perform()
    time.sleep(0.3)
    element.clear()
    element.send_keys(value)


def save_cookies(driver) -> None:
    os.makedirs(os.path.dirname(COOKIES_FILE) or ".", exist_ok=True)
    with open(COOKIES_FILE, "w") as f:
        json.dump(driver.get_cookies(), f)


def load_cookies(driver) -> bool:
    if not os.path.exists(COOKIES_FILE):
        return False
    driver.get("https://www.threads.com")
    time.sleep(2)
    with open(COOKIES_FILE) as f:
        for cookie in json.load(f):
            try:
                driver.add_cookie(cookie)
            except Exception:
                pass
    driver.refresh()
    time.sleep(3)
    return "login" not in driver.current_url


def login(driver) -> bool:
    if load_cookies(driver):
        return True

    driver.get("https://www.threads.com/login")
    time.sleep(4)
    try:
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.XPATH, "//input[not(@type='hidden')]"))
        )
        time.sleep(1)

        inputs = driver.find_elements(By.XPATH, "//input[not(@type='hidden')]")
        if len(inputs) < 2:
            raise Exception(f"Found only {len(inputs)} input fields")

        _set_input_value(driver, inputs[0], os.environ["THREADS_USERNAME"])
        time.sleep(1)
        _set_input_value(driver, inputs[1], os.environ["THREADS_PASSWORD"])
        time.sleep(1)

        clicked = False
        for btn_text in ["Войти", "Log in", "Log In", "Вход"]:
            try:
                btn = driver.find_element(By.XPATH, f"//*[text()='{btn_text}']")
                driver.execute_script("arguments[0].click();", btn)
                clicked = True
                break
            except Exception:
                continue

        if not clicked:
            inputs[1].send_keys(Keys.RETURN)

        WebDriverWait(driver, 25).until(
            lambda d: "login" not in d.current_url
        )
        save_cookies(driver)
        return True
    except Exception:
        os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
        driver.save_screenshot(os.path.join(SCREENSHOTS_DIR, "login_error.png"))
        return False
