# Topic planner — design

**Date:** 2026-09-16


## Context

ContentAgent (Kimi `post_writer`) нашёл удачную тему (производство: посты со score 125 и 13.9) и начал писать почти только про производство. Все 15 опубликованных постов — `utp_cta`.

Причина: при каждом запуске агент видит только top-5 по score и 30 последних постов, и там почти одно производство. Понятия «сфера бизнеса» в данных нет, поэтому ни код, ни LLM не видят, что другие сферы давно не затрагивались. Доли категорий из SPEC («доли задаются в playbook») тоже не реализованы.

Цель: развивать сферы с хорошим откликом и при этом регулярно писать про другие сферы, где нужна автоматизация. Решает код до вызова LLM, а не сама модель.

Решения, согласованные с пользователем:
- список сфер фиксированный в settings, LLM может предлагать новые;
- в тот же механизм входит баланс категорий;
- доли адаптивные (вес по отклику и давности, ограничение для лидера);
- подход A: планировщик в коде.

## Данные (одна Alembic-миграция в `migrations/versions/`)

- `posts.sector TEXT NULL` + модель `Post.sector` в `src/db/models.py`.
- Новая таблица `sectors`: `id`, `name TEXT UNIQUE NOT NULL` (lowercase, trimmed), `source TEXT NOT NULL` (`seed` | `llm`), `active BOOL NOT NULL DEFAULT true`, `created_at`.
- `config/settings.yaml`:
  ```yaml
  topic_planner:
    seed_sectors: [производство, розничная торговля, логистика и доставка,
                   клиники и медицина, HoReCa, недвижимость, услуги и сервис,
                   онлайн-школы, строительство, e-commerce]
    category_mix: { utp_cta: 0.5, educational: 0.3, personal: 0.1, news: 0.1 }
    window_posts: 12
    max_sector_share: 0.5
    prior_strength: 3
    new_sector_prob: 0.1
  ```
- `seed_sectors` синхронизируется в таблицу идемпотентно, только вставкой (upsert по name, существующие сферы не деактивируются). Вызывается в начале `plan_next_post`.

## Планировщик: `src/content/topic_planner.py`

`plan_next_post(session, rng=random) -> Assignment(sector: str | None, category: str, is_new_sector: bool)`. Логика на чистом Python, без LLM. Функции расчёта весов сделать чистыми, чтобы тестировать без БД.

«Окно» — последние `window_posts` постов со статусом `scheduled`, `published` или `needs_review`, сортировка по `created_at desc`.

1. С вероятностью `new_sector_prob` вернуть `Assignment(sector=None, is_new_sector=True)` (категория выбирается как в шаге 3).
2. Вес сферы:
   - `M` — общая медиана score опубликованных постов (если нет данных, `M = 1`);
   - для сферы: `n` — число опубликованных постов со score, `m` — их **среднее** (не медиана: медиана прячет единичный вирусный пост с лидами, а это и есть отклик, который нужно развивать; перекос ограничивает `max_sector_share`);
   - `shrunk = (n·m + k·M) / (n + k)`, `k = prior_strength`;
   - `perf_w = max(shrunk / M, 0.2)`;
   - `d` — сколько постов назад в окне/истории была эта сфера (если не было ни разу, `d = window`); `recency_w = 0.5 + min(d, window)/window`;
   - если доля сферы в окне `≥ max_sector_share`, вес = 0;
   - `weight = perf_w · recency_w`; выбор `rng.choices` по весам. Если все веса нулевые, берётся сфера с максимальным `d`.
3. Вес категории: `target_share² / max(actual_share_in_window, 0.05)` (квадрат в числителе не случаен: `target_share / actual_share` равновесится при `actual_share ∝ sqrt(target_share)`, а не при `actual_share = target_share`); выбор `rng.choices`.

## Интеграция в ContentAgent (`src/agents/content.py`)

- `ContentAgent.__init__(assignment: Assignment | None = None, ...)`. Если `assignment` не передан (старые тесты, ручной запуск), агент работает как раньше.
- `src/scheduler.py::run_content_agent_if_queue_low`: `plan_next_post()` → `ContentAgent(assignment=a).run(trigger="queue_low")`. Задание записывается в `agent_runs.output_ref` (или в первый шаг) для видимости в дашборде.
- В начало `TOOL_SELECTION_PROMPT` добавляется блок `ЗАДАНИЕ` через `.replace` (не `.format`, см. комментарий в коде): сфера и категория, «пиши про конкретную боль этой сферы, не меняй сферу». Для `is_new_sector` текст такой: «выбери сферу бизнеса, которой нет в списке [...], передай в save_draft(sector=...)».
- `get_top_performers(session, n, sector=None)` в `src/db/repo.py`. В `system_prompt()` и `assemble_system_prompt` (`src/prompt/assembler.py`) примеры делятся на «Лучшие в этой сфере» и «Лучшие в целом (переноси приёмы, а не тему)».
- `_tool_save_draft(text, category, source_url=None, sector=None)`:
  - если есть задание, `category` и `sector` берутся из него, а аргументы LLM игнорируются;
  - при `is_new_sector` sector нормализуется (lower/strip). Если сферы нет в таблице, вставляется с `source='llm'`;
  - `_persist_post` пишет `sector`;
  - style_critic не меняется.

## Существующие посты

`scripts/backfill_post_sectors.py`: одноразовый идемпотентный скрипт. Для постов с `sector IS NULL` роль `classifier` получает текст поста и список активных сфер и возвращает одну сферу из списка. Ответ валидируется по списку; при невалидном ответе пост пропускается и логируется. Скрипт запускается вручную после миграции.

## Дашборд

- API: `sector` в `src/api/schemas.py` (Post), фильтр `sector` в `src/api/routers/posts.py`.
- Новый эндпоинт `GET /sectors`: сфера, source, active, число постов, медиана score, дата последнего поста, текущий вес по планировщику (переиспользовать функции весов из topic_planner).
- Frontend: колонка «Сфера» и фильтр в `dashboard/app/(dashboard)/posts/page.tsx`; новая страница `dashboard/app/(dashboard)/sectors/page.tsx` плюс пункт навигации по образцу существующих страниц.

## Тесты

- `tests/content/test_topic_planner.py` (фиксированный rng):
  - сфера с долей ≥ max_share получает вес 0;
  - у сферы без постов максимальный recency;
  - сглаживание: один пост со score 125 не даёт perf_w больше ожидаемого;
  - категория с нехваткой получает больший вес;
  - `new_sector_prob` срабатывает;
  - синхронизация seed идемпотентна.
- `tests/agents/test_content.py`:
  - задание попадает в промпт;
  - save_draft сохраняет sector и category из задания вопреки аргументам LLM;
  - новая сфера вставляется с `source='llm'`;
  - без задания поведение прежнее.
- `tests/test_scheduler.py`: планировщик вызывается, задание передаётся агенту.
- `tests/api`: фильтр по sector, эндпоинт `/sectors`.
- `tests/db`: миграция/схема (по образцу существующих conftest).

## Деплой

Миграцию применить до запуска воркера с новым образом (gotcha из коммита 9ae0d61). Затем запустить `python -m scripts.backfill_post_sectors`.

## Verification

1. `pytest` — все тесты зелёные.
2. Локально: миграция → backfill → `plan_next_post` 30 раз на текущих данных (dry-run скрипт или REPL). Проверить, что производство ≈ ≤50%, остальные сферы появляются, категории разнообразны.
3. В дашборде: колонка и фильтр «Сфера», страница «Сферы» с весами.
4. После нескольких реальных запусков агента: в `agent_runs` видно задание, у новых постов заполнен `sector`, сферы и категории чередуются.

## Порядок после одобрения

1. Записать этот дизайн в `docs/superpowers/specs/2026-09-16-topic-planner-design.md`, закоммитить.
2. Skill `superpowers:writing-plans` → `docs/superpowers/plans/2026-09-16-topic-planner.md`.
3. Реализация по плану (TDD).
