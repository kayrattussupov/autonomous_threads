"""Dry run of the topic planner against the current DB: prints how the next
N assignments would be distributed, without creating any posts. Only writes
missing seed sectors (idempotent, same as the real planner does).

Run: `python -m scripts.simulate_topic_planner [N]`
"""
import random
import sys
from collections import Counter

from src.config import load_settings
from src.content.topic_planner import choose_assignment, load_planner_inputs, sync_seed_sectors
from src.db.engine import session_scope


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    cfg = load_settings()["topic_planner"]
    with session_scope() as session:
        sync_seed_sectors(session, cfg["seed_sectors"])
        session.flush()
        inputs = load_planner_inputs(session, cfg)

    rng = random.Random()
    sectors: Counter = Counter()
    categories: Counter = Counter()
    for i in range(n):
        assignment = choose_assignment(inputs, cfg, rng)
        label = assignment.sector or "<новая сфера>"
        sectors[label] += 1
        categories[assignment.category] += 1
        print(f"{i + 1:>3}. {label} / {assignment.category}")
        inputs.window = [(assignment.sector, assignment.category)] + inputs.window[: cfg["window_posts"] - 1]

    print("\nСферы:")
    for name, count in sectors.most_common():
        print(f"  {name}: {count} ({count / n:.0%})")
    print("Категории:")
    for name, count in categories.most_common():
        print(f"  {name}: {count} ({count / n:.0%})")


if __name__ == "__main__":
    main()
