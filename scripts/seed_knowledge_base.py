"""One-time (idempotent) seed of the Layer 2 knowledge_base table from
SPEC.md §7's starter content. Safe to re-run — upserts by key.

Run manually: `python -m scripts.seed_knowledge_base`
"""
from src.db.engine import session_scope
from src.db.models import KnowledgeBaseEntry

STARTER_KNOWLEDGE_BASE = {
    "niche": "автоматизация бизнес-процессов через ИИ для СМБ",
    "proof": "3 проекта: доставка, недвижимость, ресторан",
    "stack": "n8n, RAG на Qdrant, мультиагентные боты, Postgres memory",
    "differentiator": "соло, без прослойки менеджеров, быстрее агентств",
    "audience": "владельцы СМБ, маркетологи, C-level",
    "tone_seed": "инженер, который объясняет без пафоса; сухой юмор допустим",
    "never": "не обещать конкретных процентов роста без кейса",
}


def main() -> None:
    with session_scope() as session:
        for key, value in STARTER_KNOWLEDGE_BASE.items():
            row = session.get(KnowledgeBaseEntry, key)
            if row is None:
                session.add(KnowledgeBaseEntry(key=key, value=value))
            else:
                row.value = value
    print(f"Seeded {len(STARTER_KNOWLEDGE_BASE)} knowledge_base entries.")


if __name__ == "__main__":
    main()
