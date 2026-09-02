import json

from src.config import load_settings
from src.llm.client import LLMClient
from src.llm.json_extract import extract_json

CRITIC_PROMPT_TEMPLATE = """\
Ты — редактор, проверяющий пост перед публикацией в Threads.

Стилевой геном (голос, которому должен соответствовать пост):
{genome}

Пост на проверку:
{text}

Проверь ДВЕ вещи и верни JSON {{"issues": [...]}} (пустой список, если всё
хорошо):
1. Соответствует ли пост геному (голос, ритм, табу)?
2. Есть ли в посте непроверенные числовые утверждения или статистика без
   указанного источника?

Ответь СТРОГО JSON без пояснений вокруг: {{"issues": ["строка с описанием проблемы", ...]}}
"""


def run_style_critic(
    text: str,
    category: str,
    source_url: str | None,
    genome: str,
    recent_post_texts: list[str],
    llm_client: LLMClient,
    run_id: int | None = None,
    step_no: int | None = None,
) -> dict:
    settings = load_settings()["post_length"]
    issues = []

    if len(text) > settings["hard_max_chars"]:
        issues.append(f"превышен абсолютный лимит {settings['hard_max_chars']} символов (текущая длина {len(text)})")
    elif not (settings["min_chars"] <= len(text) <= settings["max_chars"]):
        issues.append(
            f"длина {len(text)} вне целевого диапазона {settings['min_chars']}-{settings['max_chars']} символов"
        )

    if category == "news" and not source_url:
        issues.append("category='news' требует проверенный source_url")

    if text.strip() in {p.strip() for p in recent_post_texts}:
        issues.append("точное повторение одного из последних постов")

    response = llm_client.complete(
        role="style_critic",
        messages=[{"role": "user", "content": CRITIC_PROMPT_TEMPLATE.format(genome=genome, text=text)}],
        run_id=run_id,
        step_no=step_no,
    )
    try:
        llm_issues = json.loads(extract_json(response.text)).get("issues", [])
    except (json.JSONDecodeError, AttributeError):
        llm_issues = [f"style_critic LLM вернул невалидный JSON: {response.text[:200]!r}"]

    issues.extend(llm_issues)

    return {
        "pass": len(issues) == 0,
        "issues": issues,
        "tokens_in": response.tokens_in,
        "tokens_out": response.tokens_out,
        "cost_usd": response.cost_usd,
    }
