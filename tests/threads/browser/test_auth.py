import json
from unittest.mock import MagicMock, patch

from src.threads.browser import auth


def test_save_cookies_writes_json(tmp_path, monkeypatch):
    cookies_file = tmp_path / "cookies.json"
    monkeypatch.setattr(auth, "COOKIES_FILE", str(cookies_file))
    driver = MagicMock()
    driver.get_cookies.return_value = [{"name": "session", "value": "abc"}]

    auth.save_cookies(driver)

    data = json.loads(cookies_file.read_text())
    assert data == [{"name": "session", "value": "abc"}]


def test_save_cookies_creates_parent_dir(tmp_path, monkeypatch):
    cookies_file = tmp_path / "nested" / "cookies.json"
    monkeypatch.setattr(auth, "COOKIES_FILE", str(cookies_file))
    driver = MagicMock()
    driver.get_cookies.return_value = []

    auth.save_cookies(driver)

    assert cookies_file.exists()


def test_load_cookies_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "COOKIES_FILE", str(tmp_path / "nonexistent.json"))
    driver = MagicMock()

    result = auth.load_cookies(driver)

    assert result is False
    driver.get.assert_not_called()


def test_load_cookies_valid_session(tmp_path, monkeypatch):
    cookies_file = tmp_path / "cookies.json"
    cookies_file.write_text(json.dumps([{"name": "session", "value": "abc"}]))
    monkeypatch.setattr(auth, "COOKIES_FILE", str(cookies_file))
    driver = MagicMock()
    driver.current_url = "https://www.threads.com/feed"

    with patch.object(auth.time, "sleep"):
        result = auth.load_cookies(driver)

    assert result is True


def test_load_cookies_invalid_session(tmp_path, monkeypatch):
    cookies_file = tmp_path / "cookies.json"
    cookies_file.write_text(json.dumps([{"name": "session", "value": "abc"}]))
    monkeypatch.setattr(auth, "COOKIES_FILE", str(cookies_file))
    driver = MagicMock()
    driver.current_url = "https://www.threads.com/login"

    with patch.object(auth.time, "sleep"):
        result = auth.load_cookies(driver)

    assert result is False


def test_login_uses_cookies_first(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "COOKIES_FILE", str(tmp_path / "cookies.json"))
    driver = MagicMock()

    with patch.object(auth, "load_cookies", return_value=True) as mock_load:
        result = auth.login(driver)

    assert result is True
    mock_load.assert_called_once_with(driver)
    driver.get.assert_not_called()


def test_login_falls_back_to_password_using_env_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "COOKIES_FILE", str(tmp_path / "cookies.json"))
    monkeypatch.setenv("THREADS_USERNAME", "user@example.com")
    monkeypatch.setenv("THREADS_PASSWORD", "hunter2")
    driver = MagicMock()
    driver.current_url = "https://www.threads.com/feed"
    mock_input = MagicMock()
    driver.find_elements.return_value = [mock_input, mock_input]
    driver.find_element.side_effect = Exception("not found")

    with patch.object(auth, "load_cookies", return_value=False), \
         patch.object(auth.time, "sleep"), \
         patch.object(auth, "WebDriverWait") as mock_wait, \
         patch.object(auth, "ActionChains"), \
         patch.object(auth, "save_cookies") as mock_save:
        mock_wait.return_value.until.return_value = True
        result = auth.login(driver)

    assert result is True
    mock_input.send_keys.assert_any_call("hunter2")
    mock_save.assert_called_once_with(driver)


def test_login_saves_screenshot_on_error(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "COOKIES_FILE", str(tmp_path / "cookies.json"))
    monkeypatch.setattr(auth, "SCREENSHOTS_DIR", str(tmp_path / "screenshots"))
    monkeypatch.setenv("THREADS_USERNAME", "user@example.com")
    monkeypatch.setenv("THREADS_PASSWORD", "hunter2")
    driver = MagicMock()
    driver.current_url = "https://www.threads.com/login"

    with patch.object(auth, "load_cookies", return_value=False), \
         patch.object(auth.time, "sleep"), \
         patch.object(auth, "WebDriverWait", side_effect=Exception("timeout")):
        result = auth.login(driver)

    assert result is False
    driver.save_screenshot.assert_called_once()
    assert (tmp_path / "screenshots").exists()
