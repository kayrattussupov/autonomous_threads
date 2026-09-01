"""T0.2: generate 10 Threads posts each from glm-4.7 and kimi-k2.5 using the
same prompt, then write them out shuffled and unlabeled for a blind human
read. Run manually: `python -m scripts.compare_models_ru`.

Acceptance (SPEC.md T0.1): >= 7/10 posts from the CHOSEN model must be
publishable without edits. That judgment is made by a human reading
docs/model_comparison_ru.md — this script only produces the blind sample.
"""
import os
import random
import yaml
from openai import OpenAI

PROMPT = (
    "Ты — соло-разработчик автоматизации бизнес-процессов на ИИ (n8n, RAG на "
    "Qdrant, мультиагентные боты, Postgres memory). Есть три завершённых "
    "проекта: доставка, недвижимость, ресторан. Позиционирование: соло, без "
    "прослойки менеджеров, быстрее агентств. Напиши один пост для Threads "
    "(до 500 символов) на тему автоматизации бизнеса для СМБ. Регистр: "
    "инженер, который объясняет без пафоса, сухой юмор допустим. Не "
    "используй выдуманные цифры и статистику."
)

CANDIDATES = [
    {"label": "glm-4.7", "provider": "glm", "base_url": "https://api.z.ai/api/paas/v4", "key_env": "GLM_API_KEY", "model": "glm-4.7"},
    {"label": "kimi-k2.5", "provider": "kimi", "base_url": "https://api.moonshot.ai/v1", "key_env": "KIMI_API_KEY", "model": "kimi-k2.5"},
]


def generate(candidate: dict, n: int = 10) -> list[str]:
    client = OpenAI(base_url=candidate["base_url"], api_key=os.environ[candidate["key_env"]])
    posts = []
    for _ in range(n):
        resp = client.chat.completions.create(
            model=candidate["model"],
            messages=[{"role": "user", "content": PROMPT}],
            max_tokens=300,
            temperature=1.0,
        )
        posts.append(resp.choices[0].message.content.strip())
    return posts


def main() -> None:
    entries = []
    for candidate in CANDIDATES:
        for post in generate(candidate):
            entries.append({"model": candidate["label"], "text": post})

    random.shuffle(entries)

    lines = ["# T0.2 — blind Russian quality comparison", "", "Read each post. Mark publishable-without-edits or not. Reveal the model key at the bottom only after judging all 20.", ""]
    for i, entry in enumerate(entries, start=1):
        lines.append(f"## Post {i}")
        lines.append(entry["text"])
        lines.append("")
        lines.append("Publishable without edits? [ ] yes [ ] no")
        lines.append("")

    lines.append("---")
    lines.append("## Key (do not read until all 20 are judged)")
    for i, entry in enumerate(entries, start=1):
        lines.append(f"- Post {i}: `{entry['model']}`")

    with open("docs/model_comparison_ru.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"Wrote {len(entries)} posts to docs/model_comparison_ru.md")


if __name__ == "__main__":
    main()
