"""Chooses the (sector, category) for the next ContentAgent run. Pure Python,
no LLM: sectors with good feedback keep getting posts, but a share cap and a
recency bonus force regular posts about other sectors too.
See docs/superpowers/specs/2026-09-16-topic-planner-design.md."""
import json
import random
import statistics
from dataclasses import asdict, dataclass

from sqlalchemy.orm import Session

from src.config import load_settings
from src.db.repo import (
    get_active_sector_names,
    get_last_post_at_by_sector,
    get_or_create_sector,
    get_planner_window,
    get_published_scores,
    list_sectors,
)

MIN_PERF_WEIGHT = 0.2
MIN_CATEGORY_SHARE = 0.05


@dataclass(frozen=True)
class Assignment:
    sector: str | None
    category: str
    is_new_sector: bool = False

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


@dataclass
class PlannerInputs:
    sectors: list[str]
    window: list[tuple[str | None, str]]  # (sector, category), newest first
    scores_by_sector: dict[str, list[float]]
    overall_median: float


def normalize_sector(name: str) -> str:
    return " ".join(name.split()).lower()


def performance_weight(scores: list[float], overall_median: float, prior_strength: float) -> float:
    """Sector mean score shrunk toward the overall median, relative to it.
    Mean rather than median on purpose: a single viral post that brought
    leads IS the feedback worth exploiting; max_sector_share bounds it."""
    n = len(scores)
    mean = statistics.fmean(scores) if n else overall_median
    shrunk = (n * mean + prior_strength * overall_median) / (n + prior_strength)
    return max(shrunk / overall_median, MIN_PERF_WEIGHT)


def _posts_since(sector: str, window: list[tuple[str | None, str]], window_size: int) -> int:
    for index, (window_sector, _) in enumerate(window):
        if window_sector == sector:
            return index + 1
    return window_size


def recency_weight(posts_since: int, window_size: int) -> float:
    return 0.5 + min(posts_since, window_size) / window_size


def compute_sector_weights(inputs: PlannerInputs, cfg: dict) -> dict[str, float]:
    window_size = cfg["window_posts"]
    weights = {}
    for sector in inputs.sectors:
        share = sum(1 for window_sector, _ in inputs.window if window_sector == sector) / window_size
        if share >= cfg["max_sector_share"]:
            weights[sector] = 0.0
            continue
        perf = performance_weight(inputs.scores_by_sector.get(sector, []), inputs.overall_median, cfg["prior_strength"])
        weights[sector] = perf * recency_weight(_posts_since(sector, inputs.window, window_size), window_size)
    return weights


def compute_category_weights(category_mix: dict[str, float], window: list[tuple[str | None, str]]) -> dict[str, float]:
    total = len(window)
    weights = {}
    for category, target_share in category_mix.items():
        actual_share = sum(1 for _, window_category in window if window_category == category) / total if total else 0.0
        weights[category] = target_share / max(actual_share, MIN_CATEGORY_SHARE)
    return weights


def _weighted_choice(weights: dict[str, float], rng) -> str:
    names = list(weights)
    return rng.choices(names, weights=[weights[name] for name in names], k=1)[0]


def choose_assignment(inputs: PlannerInputs, cfg: dict, rng) -> Assignment:
    category = _weighted_choice(compute_category_weights(cfg["category_mix"], inputs.window), rng)
    if not inputs.sectors or rng.random() < cfg["new_sector_prob"]:
        return Assignment(sector=None, category=category, is_new_sector=True)

    weights = compute_sector_weights(inputs, cfg)
    if not any(weights.values()):
        window_size = cfg["window_posts"]
        sector = max(inputs.sectors, key=lambda s: _posts_since(s, inputs.window, window_size))
        return Assignment(sector=sector, category=category)
    return Assignment(sector=_weighted_choice(weights, rng), category=category)


def sync_seed_sectors(session: Session, names: list[str]) -> None:
    for name in names:
        get_or_create_sector(session, normalize_sector(name), source="seed")


def load_planner_inputs(session: Session, cfg: dict) -> PlannerInputs:
    scores = get_published_scores(session)
    scores_by_sector: dict[str, list[float]] = {}
    for sector, score in scores:
        if sector:
            scores_by_sector.setdefault(sector, []).append(score)
    all_scores = [score for _, score in scores]
    overall_median = statistics.median(all_scores) if all_scores else 1.0
    if overall_median <= 0:
        overall_median = 1.0
    return PlannerInputs(
        sectors=get_active_sector_names(session),
        window=get_planner_window(session, cfg["window_posts"]),
        scores_by_sector=scores_by_sector,
        overall_median=overall_median,
    )


def _settings(settings: dict | None) -> dict:
    return settings if settings is not None else load_settings()["topic_planner"]


def plan_next_post(session: Session, rng=None, settings: dict | None = None) -> Assignment:
    cfg = _settings(settings)
    sync_seed_sectors(session, cfg["seed_sectors"])
    session.flush()
    return choose_assignment(load_planner_inputs(session, cfg), cfg, rng or random.Random())


def describe_sectors(session: Session, settings: dict | None = None) -> list[dict]:
    """Read-only stats for the dashboard; does not sync seeds."""
    cfg = _settings(settings)
    inputs = load_planner_inputs(session, cfg)
    weights = compute_sector_weights(inputs, cfg)
    total_weight = sum(weights.values())
    last_post_at = get_last_post_at_by_sector(session)

    rows = []
    for sector in list_sectors(session):
        scores = inputs.scores_by_sector.get(sector.name, [])
        weight = weights.get(sector.name) if sector.active else None
        rows.append({
            "name": sector.name,
            "source": sector.source,
            "active": sector.active,
            "published_n": len(scores),
            "mean_score": statistics.fmean(scores) if scores else None,
            "median_score": statistics.median(scores) if scores else None,
            "last_post_at": last_post_at.get(sector.name),
            "weight": weight,
            "probability": (weight / total_weight) if weight is not None and total_weight > 0 else None,
        })
    return rows
