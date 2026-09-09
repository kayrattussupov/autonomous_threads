"""Creates a real, human-authored style_variant as a draft.

The publisher blocks real posting while the active style_variant is named
'v1_placeholder' (see src/agents/publisher.py). To get unblocked:

  1. Write the genome yourself (SPEC.md §7): 300-800 words covering voice,
     register of humor, rhythm, hook patterns, length, structure, taboos.
     Save it as a plain text/markdown file.
  2. Run: python -m scripts.create_style_variant --name v1 --genome-file path/to/genome.md
  3. Approve the printed id via the dashboard API:
       curl -X POST -H "Authorization: Bearer $API_BEARER_TOKEN" \\
            http://localhost:8000/styles/<id>/approve
  4. Retire the placeholder so it stops competing for post assignment —
     get_active_style() (src/db/repo.py) ties on posts_n and breaks ties by
     the lower id, so simply adding a second active variant is not enough;
     the placeholder (id=1, posts_n=0) would keep winning forever:
       UPDATE style_variants SET status = 'retired' WHERE name = 'v1_placeholder';
"""
import argparse

from src.db.engine import session_scope
from src.db.models import StyleVariant


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True, help="short identifier, e.g. v1")
    parser.add_argument("--genome-file", required=True, help="path to the genome text (300-800 words)")
    parser.add_argument("--rationale", default="Human-authored real genome.")
    args = parser.parse_args()

    with open(args.genome_file, encoding="utf-8") as f:
        genome = f.read().strip()

    word_count = len(genome.split())
    if not (300 <= word_count <= 800):
        print(f"warning: genome is {word_count} words, SPEC.md §7 wants 300-800")

    with session_scope() as session:
        existing = session.query(StyleVariant).filter_by(name=args.name).one_or_none()
        if existing is not None:
            print(f"style_variant '{args.name}' already exists (id={existing.id}), not creating.")
            return
        variant = StyleVariant(
            name=args.name,
            genome=genome,
            status="draft",
            created_by="human",
            rationale=args.rationale,
        )
        session.add(variant)
        session.flush()
        variant_id = variant.id

    print(f"Created draft style_variant id={variant_id} name={args.name!r} ({word_count} words).")
    print(f"Approve it: POST /styles/{variant_id}/approve (Authorization: Bearer $API_BEARER_TOKEN)")
    print("Then retire the placeholder so it stops winning post assignment ties:")
    print("  UPDATE style_variants SET status = 'retired' WHERE name = 'v1_placeholder';")


if __name__ == "__main__":
    main()
