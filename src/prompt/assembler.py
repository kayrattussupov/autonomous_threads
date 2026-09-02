def _render_knowledge_base(kb: dict) -> str:
    lines = "\n".join(f"{key} = {value}" for key, value in kb.items())
    return f"# База знаний\n{lines}"


def _render_playbook(rules: list[str]) -> str:
    if not rules:
        return "# Playbook\n(правил пока нет)"
    lines = "\n".join(f"- {rule}" for rule in rules)
    return f"# Playbook\n{lines}"


def _render_examples(swipe_examples: list[str], top_posts: list[str]) -> str:
    parts = ["# Примеры"]
    if top_posts:
        parts.append("## Твои лучшие посты")
        parts.extend(f"- {p}" for p in top_posts)
    if swipe_examples:
        parts.append("## Зашедшие посты в нише (чужие)")
        parts.extend(f"- {p}" for p in swipe_examples)
    if len(parts) == 1:
        parts.append("(примеров пока нет)")
    return "\n".join(parts)


def assemble_system_prompt(
    constitution: str,
    knowledge_base: dict,
    active_genome: str,
    playbook_rules: list[str],
    swipe_examples: list[str],
    top_posts: list[str],
) -> str:
    return "\n\n".join([
        constitution,
        _render_knowledge_base(knowledge_base),
        active_genome,
        _render_playbook(playbook_rules),
        _render_examples(swipe_examples, top_posts),
    ])
