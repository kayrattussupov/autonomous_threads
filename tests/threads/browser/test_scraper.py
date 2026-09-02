from unittest.mock import MagicMock

from src.config import load_settings
from src.threads.browser import scraper


def test_max_post_age_days_defaults_from_settings():
    assert scraper.MAX_POST_AGE_DAYS == load_settings()["max_post_age_days"]


def test_extract_age_days_hours():
    assert scraper.extract_age_days("5h") == 0


def test_extract_age_days_days():
    assert scraper.extract_age_days("3d") == 3


def test_extract_age_days_weeks():
    assert scraper.extract_age_days("2w") == 14


def test_extract_age_days_no_match():
    assert scraper.extract_age_days("hello world") is None


def test_extract_age_days_multiline():
    text = "Some post text\nAnother line\n2d\nMore content"
    assert scraper.extract_age_days(text) == 2


def test_clean_post_text_removes_timestamps():
    text = "22h\nИщу работу\n5m"
    result = scraper.clean_post_text(text)
    assert result == "Ищу работу"


def test_clean_post_text_removes_translate():
    text = "Some post\nTranslate\nMore text"
    result = scraper.clean_post_text(text)
    assert "Translate" not in result
    assert "Some post" in result


def test_clean_post_text_removes_numbers():
    text = "38\nТекст поста"
    result = scraper.clean_post_text(text)
    assert result == "Текст поста"


def test_clean_post_text_keeps_content():
    text = "Ищу работу разработчика"
    result = scraper.clean_post_text(text)
    assert result == "Ищу работу разработчика"


def test_scrape_keyword_filters_old_and_noisy_posts(monkeypatch):
    monkeypatch.setattr(scraper, "MAX_POST_AGE_DAYS", 3)
    driver = MagicMock()
    driver.execute_script.return_value = [
        {"text": "5d\nA post older than the max age", "url": "https://www.threads.com/post/old"},
        {"text": "1d\nTranslate\nA fresh post that survives filtering", "url": "https://www.threads.com/post/fresh"},
    ]

    results = scraper.scrape_keyword(driver, "n8n", scroll_times=0)

    assert results == [
        {"keyword": "n8n", "text": "A fresh post that survives filtering", "url": "https://www.threads.com/post/fresh"}
    ]
