import re
import time
from datetime import date, timedelta
from urllib.parse import quote

from src.config import load_settings

MAX_POST_AGE_DAYS = load_settings()["max_post_age_days"]

_AGE_RE = re.compile(r'^(\d+)([smhdwy])$', re.IGNORECASE)
_AGE_MULTIPLIERS = {'s': 0, 'm': 0, 'h': 0, 'd': 1, 'w': 7, 'y': 365}

_NOISE_PATTERNS = [
    r'^\d+[smhdwmy]\s*$',   # timestamps: 22h, 5m, 1d, 3w
    r'^Translate$',          # translate button
    r'^[\d\s/]+$',           # pure number lines: "38", "1 / 2"
    r'^\d+\s*/\s*\d+$',      # pagination: "1 / 2"
]
_NOISE_RE = re.compile('|'.join(_NOISE_PATTERNS), re.IGNORECASE)

_SCRAPE_POSTS_JS = """
    const results = [];
    const seenUrls = new Set();

    document.querySelectorAll('a[href*="/post/"]').forEach(link => {
        const url = link.href;
        if (seenUrls.has(url)) return;

        let text = '';
        let node = link.parentElement;
        for (let i = 0; i < 20; i++) {
            if (!node || node === document.body) break;
            const t = node.innerText?.trim();
            if (t && t.length > 30) {
                text = t;
                break;
            }
            node = node.parentElement;
        }

        if (text) {
            seenUrls.add(url);
            results.push({text: text.slice(0, 500), url});
        }
    });
    return results;
"""


def extract_age_days(text: str) -> int | None:
    for line in text.splitlines():
        m = _AGE_RE.match(line.strip())
        if m:
            val, unit = int(m.group(1)), m.group(2).lower()
            return val * _AGE_MULTIPLIERS[unit]
    return None


def clean_post_text(text: str) -> str:
    lines = text.splitlines()
    clean = [line for line in lines if not _NOISE_RE.match(line.strip())]
    return '\n'.join(clean).strip()


def scrape_keyword(driver, keyword: str, scroll_times: int = 5, url_template: str | None = None, group_name: str = "") -> list[dict]:
    after_date = (date.today() - timedelta(days=MAX_POST_AGE_DAYS)).isoformat()
    if url_template:
        url = url_template.format(date=after_date, keyword=quote(keyword))
    else:
        url = f"https://www.threads.com/search?after_date={after_date}&q={quote(keyword)}&serp_type=default&filter=recent"
    driver.get(url)
    time.sleep(5)

    for _ in range(scroll_times):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(3)

    items = driver.execute_script(_SCRAPE_POSTS_JS)

    results = []
    for item in (items or []):
        raw_text = item["text"]
        age_days = extract_age_days(raw_text)
        if age_days is not None and age_days > MAX_POST_AGE_DAYS:
            continue
        text = clean_post_text(raw_text)
        if not text:
            continue
        results.append({"keyword": keyword, "text": text, "url": item.get("url")})

    return results
