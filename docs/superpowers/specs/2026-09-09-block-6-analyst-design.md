# DESIGN: Block 6 — `analyst_agent` (T6.1, T6.2, T6.3)

**Дата:** 2026-09-09 · **Статус:** черновик, ждёт ревью
**Родитель:** [SPEC.md](../../../SPEC.md) §6.5, §7 (Слой 3, Слой 4, целевая функция), §8, §11 Блок 6

## 1. Контекст

Блок 6 — три задачи: T6.1 (ночной пересчёт insights/score по всем живым постам), T6.2 (`analyst_agent`, ReAct, с инструментами `propose_playbook_diff`/`propose_style_variant`), T6.3 (первый месячный отчёт проверен человеком — процессный шаг, не код сам по себе). Единый дизайн, т.к. T6.1 и T6.2 делят одну и ту же модель данных и запускаются под одним именем агента (`agent_runs.agent = "analyst"`).

**Что уже есть в кодовой базе и переиспользуется:**
- `src/agents/base.py::ReActAgent` — харнесс с лимитами шагов/токенов/времени, трассировкой в `agent_runs`/`agent_steps`. `src/agents/content.py` — эталон ReAct-агента (system_prompt + decide_next_action через LLM JSON tool-call + tools dict).
- `src/agents/feed_miner.py` — эталон детерминированного (не-ReAct) пайплайна: `run_X(trigger, ...) -> dict`, короткие `session_scope()` на шаг, ручной учёт токенов/стоимости.
- `config/models.yaml` — роль `analyst` (`kimi-k2.6`, `max_tokens: 8000`) уже сконфигурирована, ничем не используется.
- Таблицы `style_variants` и `playbook_rules` уже в схеме (`src/db/models.py`), уже есть полный CRUD + human-approval flow в API и дашборде (Блок 4, уже реализован):
  - `repo.approve_style_variant`/`reject_style_variant` — `draft → active`, с вытеснением худшего активного варианта по медиане (запрещено при `posts_n < 20`).
  - `repo.approve_playbook_rule`/`reject_playbook_rule` — `proposed → testing` / `proposed → rejected`.
  - API-роутеры `src/api/routers/styles.py`, `playbook.py` и дашборд-страницы `dashboard/app/(dashboard)/styles`, `.../playbook` уже вызывают эти функции.
- `repo.get_active_playbook_rules` — активные правила = `status in ('testing', 'confirmed')`. Это определение "активного" правила для потолка в 12 штук.
- `src/threads/write_client.py::get_media_insights(media_id)` — уже есть, используется публикацией метрик поста.
- `src/alerts.py::send_telegram_alert` — единая конвенция алертов по всей кодовой базе.

**Чего не хватает и добавляется в этом блоке:**
- Модуль `src/agents/analyst.py` целиком: `recompute_nightly_metrics()` и `AnalystAgent`.
- Пересчёт `posts.score` — нигде в кодовой базе ещё не считается (колонка существует, но не заполняется никаким кодом).
- Новый статус `playbook_rules.status = 'proposed_removal'` и его обработка в `approve_playbook_rule`/`reject_playbook_rule`.
- Потолок 12 активных правил и авто-вытеснение слабейшего — не реализовано (текущий `approve_playbook_rule` просто переводит статус без проверки лимита).
- Авто-переход `testing → confirmed` по `evidence_n`/медианам — не реализовано.
- `src/tools/safe_sql.py` — read-only SQL для LLM, новый модуль.
- Точечное расширение дашборда: кнопки апрува для `proposed_removal` (сейчас `dashboard/app/(dashboard)/playbook/page.tsx` рендерит Принять/Отклонить только при `status === "proposed"`).
- Новый ключ `config/settings.yaml: metrics_refresh_window_days: 90`.
- Новая job в `src/scheduler.py`.

## 2. Архитектура

```
APScheduler (cron, 03:00 Almaty)                 APScheduler (cron, 1-е число, 20:00 Almaty)
        │                                                    │
        ▼                                                    ▼
recompute_nightly_metrics(trigger)              AnalystAgent().run(trigger="cron")
  — детерминированный, НЕ ReActAgent               — ReActAgent, tools ниже
  — agent_runs.agent="analyst",                    — agent_runs.agent="analyst",
    trigger="nightly"                                trigger="cron"
        │                                                    │
        ├─→ ThreadsWriteClient.get_media_insights            ├─→ repo (fetch_insights, sql, get_swipe_stats)
        ├─→ пересчёт score/leads/conversations (SQL)          ├─→ repo.propose_playbook_diff / propose_style_variant
        ├─→ пересчёт evidence_n/median_after/auto-promote     └─→ send_telegram_alert (summary)
        └─→ пересчёт style_variants.median_score
```

Оба входа используют один и тот же `agent_runs.agent = "analyst"`, различаются `trigger` (`"nightly"` vs `"cron"`) — это уже согласуется с комментарием в схеме (`agent_runs.agent — content | analyst | feed_miner | reply_triage`), отдельного имени агента для ночного джоба заводить не нужно.

## 3. Ночной пересчёт (T6.1) — `recompute_nightly_metrics`

Сигнатура: `recompute_nightly_metrics(trigger: str = "cron", write_client: ThreadsWriteClient | None = None) -> dict` — DI для тестируемости, как `run_feed_miner`.

1. **Обновление сырых метрик из API.** Посты `status='published'`, `threads_media_id IS NOT NULL`, `posted_at >= now() - metrics_refresh_window_days` (новый ключ настроек, default `90`). Для каждого — `write_client.get_media_insights(media_id)`, запись `views/likes/replies_count/quotes/metrics_updated_at`. Локальный сбой одного поста (после встроенного backoff клиента) не прерывает прогон — шаг помечается failed, идём дальше (как `feed_miner`/`reply_triage`).
2. **Пересчёт score для ВСЕХ published-постов** (не только из окна — это чистый SQL по уже сохранённым `replies`, дёшево):
   - `leads(post_id)` = `count(replies where post_id=X and kind='lead')`
   - `conversations(post_id)` = `count(replies where post_id=X and kind in ('question','objection'))`
   - `score = 100*leads + 10*conversations + 1*replies_count + 0.01*views` (формула SPEC.md §7)
   - Новая `repo.recompute_all_post_scores(session) -> int` (возвращает число обновлённых строк), один SQL UPDATE с подзапросами по `replies`, без построчного Python-цикла.
3. **Пересчёт `style_variants.median_score`** — `percentile_cont(0.5)` по `posts.score` сгруппированным по `style_variant_id` (переиспользует ту же формулу, что уже есть в `repo.median_post_score`, но per-variant и с записью в таблицу, а не как read-запрос).
4. **Пересчёт `playbook_rules` для `status='testing'`:**
   - `median_before` — вычисляется **один раз**, только если ещё `NULL`: медиана `posts.score` среди `published`-постов с `posted_at < introduced_at`. Дальше не трогается (иначе база сравнения плывёт вместе со временем, и "разница медиан" теряет смысл).
   - `evidence_n` = число `published`-постов с `posted_at >= introduced_at`.
   - `median_after` = их медиана `score`.
   - **Авто-промоция:** если `evidence_n >= 20` и `median_before` не `NULL`/не `0` и `median_after >= median_before * 1.3` → `status = 'confirmed'`. Ухудшение на ≥30% НЕ авто-отклоняет правило (спека не просит; отклонение остаётся ручным через дашборд по факту наблюдения) — это осознанное решение, см. п.7.
5. **Ceiling eviction** переносится в `approve_playbook_rule` (см. §3.4 ниже), не в ночной джоб — он срабатывает в момент, когда человек одобряет 13-е правило, а не на пересчёте.

Крон: `src/scheduler.py`, `trigger="cron", hour=3, minute=0, id="analyst_nightly_recompute"`.

## 4. `AnalystAgent` (T6.2)

ReAct-агент, `agents/analyst.py`, крон `day=1, hour=20, minute=0` Asia/Almaty (Block 6, SPEC §6.5).

**Инструменты** (спека + один служебный):
```python
fetch_insights(post_ids: list[int]) -> dict        # читает из БД (уже свежая после ночного джоба)
sql(query: str) -> list[dict]                       # read-only, whitelist таблиц
get_swipe_stats(days: int = 30) -> dict              # топ-15 тем свипфайла по медиане views/likes
propose_playbook_diff(add: list, remove: list, rationale: str)
propose_style_variant(name: str, genome: str, rationale: str, parent_id: int | None)
finish(summary: str)                                 # НЕ из спеки: явный сигнал остановки
```
`finish` нужен потому, что паттерн `content_agent` (`self._done = True` после одного `save_draft`) тут не подходит — аналитик может вызвать `propose_playbook_diff` и `propose_style_variant` в одном прогоне, плюс несколько `sql`/`get_swipe_stats`/`fetch_insights` до этого. `decide_next_action` возвращает `None` только после вызова `finish`, либо (как у `content_agent`) когда исчерпаны лимиты шагов/токенов харнесса.

**`add`** в `propose_playbook_diff`: список `{rule_text, hypothesis, target_metric}` → новые строки `playbook_rules`, `status='proposed'`, `version` = `max(version)+1` глобально по таблице (версионирование сквозное, не per-rule).

**`remove`**: список существующих `id` (testing/confirmed) → `status='proposed_removal'`. Правило остаётся активным (участвует в `get_active_playbook_rules`, т.к. фильтр там — `in ('testing','confirmed')`, `proposed_removal` туда не входит — **важно:** это значит предложенное к удалению правило перестаёт применяться в промпте контент-агента ещё до апрува человеком. Альтернатива — оставить его активным до явного апрува удаления — обсуждается в п.7, выбран текущий вариант как более безопасный по умолчанию (спорное предложенное к удалению правило не продолжает молча работать месяц до следующего ревью).

**`propose_style_variant`**: новая строка `style_variants`, `status='draft'`, `created_by='analyst'`.

**`sql(query)`** — `src/tools/safe_sql.py::execute_readonly(session, query, allowed_tables) -> list[dict]`:
- Парсинг через `sqlglot` (dialect `postgres`). Требования: ровно один `SELECT`-стейтмент (никаких `;`-разделённых цепочек), никаких CTE/подзапросов к таблицам вне whitelist, запрет `INSERT/UPDATE/DELETE/DROP/ALTER/GRANT/TRUNCATE/CREATE`.
- Whitelist таблиц: `posts, swipe_file, style_variants, playbook_rules, replies, leads`.
- На невалидный/запрещённый запрос — возвращает `{"error": "..."}"` инструменту (не бросает исключение наружу — харнесс и так ловит любое исключение из tool-вызова и продолжает прогон, но здесь хотим дать модели понятную обратную связь для повторной попытки в рамках того же прогона).
- Ограничение размера результата (`LIMIT 200` принудительно добавляется, если в запросе нет своего `LIMIT`) — защита от случайного полного дампа таблицы в контекст модели.

**System prompt** — отдельный от `assemble_system_prompt` (тот собран под писателя постов, аналитику не подходит). Новый `ANALYST_SYSTEM_PROMPT`/`TOOL_SELECTION_PROMPT` в `analyst.py`: цель (целевая функция §7, медиана не среднее, радикальные варианты статистически предпочтительнее осторожных правок — прямая цитата из SPEC.md §7), описание инструментов, и тот же prompt-injection-defense абзац, что уже есть у `content_agent` про `web_search` — здесь применим к `sql`/`get_swipe_stats`, т.к. `swipe_file` содержит чужие посты (внешний текст).

**Алерт по завершении** (T6.3): в конце `run()` — `send_telegram_alert` с summary: сколько предложено стилей/правил-добавлений/правил-на-удаление, по каждому — короткий rationale. Это практический hook для "человек проверяет первый месячный отчёт" — сам ручной ревью вне кода.

## 5. Изменения в `repo.py`

- `recompute_all_post_scores(session) -> int`
- `recompute_style_variant_medians(session) -> None`
- `recompute_playbook_evidence(session) -> list[PlaybookRule]` (возвращает те, что авто-промоутнулись, для логирования в `agent_steps`)
- `get_swipe_stats(session, days: int = 30) -> list[dict]`
- `propose_playbook_diff(session, add: list[dict], remove: list[int], rationale: str) -> dict`
- `propose_style_variant(session, name, genome, rationale, parent_id) -> StyleVariant`
- `approve_playbook_rule` — расширяется: ветвление по `status` (`proposed` → `testing` с ceiling-check; `proposed_removal` → `rejected`), иначе `InvalidStateTransition` как сейчас.
- `reject_playbook_rule` — расширяется: `proposed` → `rejected` (как сейчас); `proposed_removal` → откат к `confirmed`, если `evidence_n >= 20 and median_after >= median_before*1.3`, иначе `testing` (не нужна отдельная колонка "статус до удаления" — выводится из тех же полей, что использует авто-промоция).
- Ceiling eviction — helper `_evict_weakest_active_rule(session)`, вызывается из `approve_playbook_rule` при переходе `proposed → testing`, если активных (testing+confirmed) уже 12: вытесняет правило с наименьшим `median_after` (fallback `median_before`, если `median_after IS NULL`; тай-брейк — наименьший `introduced_at`) → `status='rejected'`.

## 6. Дашборд (точечно)

`dashboard/app/(dashboard)/playbook/page.tsx`: условие рендера кнопок меняется с `rule.status === "proposed"` на `rule.status === "proposed" || rule.status === "proposed_removal"`; для `proposed_removal` подписи кнопок — «Одобрить удаление» / «Отменить удаление» (той же серверной экшен-парой `approveAction`/`rejectAction`, эндпоинты те же `/playbook/{id}/approve|reject`). Без изменений в `api-client.ts`/типах — `status` уже типизирован как `string`.

## 7. Обозначенные допущения (спека не даёт прямого ответа)

- Окно 90 дней для обновления инсайтов через API — баланс между бюджетом API-вызовов и полнотой данных; score-пересчёт из уже сохранённых данных не ограничен окном.
- `median_before` замораживается при первом появлении evidence, не пересчитывается каждую ночь.
- Авто-промоция только при УЛУЧШЕНИИ ≥30% (не любом отклонении); ухудшение не триггерит авто-действий.
- «Слабейшее» правило при вытеснении = наименьший `median_after`.
- `proposed_removal` немедленно исключает правило из `get_active_playbook_rules` (перестаёт применяться в промпте) ещё до апрува удаления человеком.
- Whitelist таблиц для `sql()`: `posts, swipe_file, style_variants, playbook_rules, replies, leads` (не включает `knowledge_base`, трейсинг-таблицы, `daily_spend/daily_limits`).
- `playbook_rules.version` — трактуется как сквозной монотонный счётчик по всей таблице (`max(version)+1` на каждое новое правило), а не как номер "снапшота" всего плейбука. Это не связано с `posts.playbook_version`, который ни один агент в кодовой базе сейчас не заполняет (см. п.10) — Блок 6 эту связь не восстанавливает, т.к. это не входит в приёмку T6.1–T6.3.

## 8. Ошибки

| Ситуация | Поведение |
|---|---|
| `get_media_insights` падает для одного поста (после backoff клиента) | Пропустить пост, шаг failed, ночной прогон продолжается |
| `get_media_insights` падает системно (auth/permission) | Остановка прогона, `status='failed'`, алерт |
| `sql()` — невалидный/запрещённый запрос | Возврат `{"error": ...}` инструменту, шаг `tool_ok=False`, ReAct-цикл продолжается (модель может скорректировать запрос) |
| `BudgetExceeded` в `AnalystAgent` | `status='budget_stop'`, алерт (как у всех агентов, обрабатывается харнессом `base.py`) |
| Лимит шагов/токенов/времени харнесса исчерпан до вызова `finish` | `status='step_limit'`, никакие `propose_*`-результаты не теряются — они уже записаны в БД к этому моменту как побочный эффект вызова инструмента, теряется только сам финальный summary-алерт |
| Любое непредвиденное исключение | `status='failed'`, алерт "unexpected error" (как у `feed_miner`/`reply_triage`) |

## 9. Тестирование

По образцу `tests/agents/test_feed_miner.py` и `tests/agents/test_content.py`:
- `tests/tools/test_safe_sql.py`: разрешённый `SELECT` по whitelist-таблице проходит; `INSERT/UPDATE/DELETE/DROP` отклоняются; запрос вне whitelist отклоняется; `;`-цепочка стейтментов отклоняется; отсутствующий `LIMIT` получает принудительный `LIMIT 200`.
- `tests/db/test_repo_analyst.py` (или расширение существующего repo-теста): пересчёт score из `replies.kind`, заморозка `median_before`, авто-промоция при `evidence_n>=20` и улучшении ≥30% (и НЕ-промоция при <30% или недостаточном `evidence_n`), ceiling eviction при 13-м активном правиле, `propose_playbook_diff` создаёт `proposed`/`proposed_removal` корректно, апрув/реджект `proposed_removal` откатывает в правильный статус.
- `tests/agents/test_analyst.py`: `recompute_nightly_metrics` — happy path, локальный сбой одного поста не прерывает прогон, системный сбой останавливает прогон с алертом. `AnalystAgent` — мок LLM, проверка что `propose_style_variant`/`propose_playbook_diff`/`finish` вызывают нужные repo-функции и завершают прогон корректным статусом; `BudgetExceeded` → `budget_stop`.

## 10. Вне scope этого дизайна

- T6.3 как таковой (ручной sanity-check человеком первого отчёта) — процессный шаг, не код; код даёт только Telegram-алерт как триггер для этого шага.
- Изменение `content_agent`/`assemble_system_prompt` — не трогается, аналитик использует отдельный system prompt.
- Блок 7 (холодные лиды, `lead_scorer`) — не начат, явно "не раньше" по декомпозиции.
- Ретеншн `prompt_raw`/`agent_steps` (упомянут в SPEC.md §8 как "ночной джоб", но не привязан к конкретному блоку декомпозиции) — отдельная задача, не включена сюда.
- UI-визуализация генеалогии стилевых вариантов (`parent_id` — граф/дерево) — экран уже существует (Блок 4), новых полей `propose_style_variant` не добавляет сверх того, что API/дашборд уже умеют показывать.
- Заполнение `posts.playbook_version` при создании поста — колонка существует в схеме, но её не пишет ни один агент (ни `content_agent` сейчас, ни что-либо в этом дизайне). Предсуществующий пробел, не входит в приёмку T6.1–T6.3.
