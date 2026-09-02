"""Seeds a PLACEHOLDER v1 style variant so the content pipeline is runnable
end-to-end before the human operator has written their real voice.

THIS GENOME TEXT IS A STAND-IN, NOT THE REAL VOICE. SPEC.md §7 requires the
Layer 3 genome to be human-authored (style_variants.created_by='human') —
replace PLACEHOLDER_GENOME below (or UPDATE the row directly) with the real
300-800 word genome before any post generated against it is actually
scheduled for real, non-test publishing.

Run manually, once: `python -m scripts.seed_style_variant_v1`
"""
from src.db.engine import session_scope
from src.db.models import StyleVariant

PLACEHOLDER_GENOME = """\
[ВРЕМЕННЫЙ ГЕНОМ — заменить перед реальной публикацией]

Голос: инженер, который объясняет без пафоса. Сухой юмор допустим, но не
обязателен в каждом посте. Никакого "мотивационного" тона, никаких
восклицательных знаков подряд.

Ритм: короткие предложения. Один пост — одна мысль. Без вступлений вроде
"Сегодня хочу рассказать про...".

Хуки: начинать с конкретного наблюдения, вопроса или факта — не с общих слов
об "автоматизации будущего".

Длина: 200-400 символов, без исключений в эту сторону; если мысль не
помещается — сократить, а не растягивать до лимита.

Структура: тезис → короткое обоснование или пример → (опционально) один CTA.

Табу: превосходные степени без обоснования ("лучший", "уникальный"),
обещания процентов роста без кейса, эмодзи как замена мысли.
"""


def main() -> None:
    with session_scope() as session:
        existing = session.query(StyleVariant).filter_by(name="v1_placeholder").one_or_none()
        if existing is not None:
            print(f"style_variant 'v1_placeholder' already exists (id={existing.id}), not re-seeding.")
            return
        variant = StyleVariant(
            name="v1_placeholder",
            genome=PLACEHOLDER_GENOME,
            status="active",
            created_by="human",
            rationale="Placeholder seeded by Block 3 setup — REPLACE with the operator's real authored genome before production posting.",
        )
        session.add(variant)
        session.flush()
        variant_id = variant.id
    print(f"Seeded placeholder style_variant id={variant_id}. REPLACE ITS GENOME before real publishing:")
    print(f'  UPDATE style_variants SET genome = \'...\' WHERE id = {variant_id};')


if __name__ == "__main__":
    main()
