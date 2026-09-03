# DESIGN: Block 5 — `reply_triage` (T5.1)
**Дата:** 2026-09-04 · **Статус:** черновик, ждёт ревью
**Родитель:** [SPEC.md](../../../SPEC.md) §6.4, §9, §11 Блок 5 (T5.1)

## 1. Контекст

Блок 5 в SPEC.md состоит из двух задач: T5.1 (`reply_triage` — сбор, классификация, черновики, алерт на лида) и T5.2 (апрув и отправка ответов из Telegram). Это разные по форме вещи: T5.1 — детерминированный пайплайн-агент, такой же по структуре, как уже реализованный `feed_miner` (Блок 2). T5.2 — интерактивный Telegram-бот (webhook/polling, callback-кнопки), качественно другая задача.

Этот документ — дизайн только T5.1. T5.2 будет отдельным под-проектом со своим брейнштормом, после того как T5.1 покажет, что входящие успевают обрабатываться за 3 часа.

**Что уже есть в кодовой базе и переиспользуется:**
- `src/agents/feed_miner.py` — эталонная форма детерминированного (не-ReAct) пайплайна: `run_X(trigger, ...) -> dict`, трассировка в `agent_runs`/`agent_steps` через `start_agent_run`/`add_agent_step`/`finish_agent_run`, ручной подсчёт токенов/стоимости, `try/except` по каждому внешнему вызову, без ретраев на систем­ных ошибках.
- `config/models.yaml` — роли `classifier` (200 max_tokens) и `commenter` (500 max_tokens) уже определены; `classifier` уже используется `feed_miner`, `commenter` пока не используется нигде.
- `src/alerts.py::send_telegram_alert(text: str) -> bool` — никогда не бросает исключение, уже используется по всей кодовой базе с единой конвенцией (f-string с именем агента).
- `src/db/models.py::Reply` — таблица уже есть в схеме (id, threads_reply_id, post_id, author_username, text, kind, draft_response, status, received_at, responded_at), но в `repo.py` для неё нет ни одной CRUD-функции.
- `src/db/models.py::Lead` — таблица уже есть (threads_username, source_url, score, score_reason, status), пока не используется никаким агентом.
- `src/db/repo.py::get_knowledge_base(session) -> dict` — уже существует, используется `content_agent`.

**Чего не хватает и нужно добавить:** ни `ThreadsWriteClient`, ни `ThreadsReadClient` не умеют забирать сами ответы (текст/автора) под своими постами — есть только агрегированный счётчик `replies` в `get_media_insights()`. Это добавляется в рамках T5.1.

## 2. Архитектура

```
APScheduler (interval, 3h)
        │
        ▼
run_reply_triage(trigger, write_client=None, llm_client=None) -> dict
  — детерминированный пайплайн, НЕ ReActAgent (как feed_miner, SPEC.md §12)
  — трассируется в agent_runs/agent_steps так же, как ReAct-агенты
        │
        ├─→ ThreadsWriteClient.get_replies(media_id)   [новый метод]
        ├─→ LLMClient.complete(role="classifier", ...)
        ├─→ LLMClient.complete(role="commenter", ...)
        └─→ send_telegram_alert(...)
```

Модуль `src/agents/reply_triage.py`, единственная публичная функция `run_reply_triage(trigger: str = "cron", write_client: ThreadsWriteClient | None = None, llm_client: LLMClient | None = None) -> dict` — сигнатура и внутренняя структура зеркалят `run_feed_miner` (dependency injection для тестируемости, короткие `session_scope()` на каждый шаг, а не одна долгая сессия на весь прогон).

Регистрация в `src/scheduler.py`:
```python
scheduler.add_job(
    run_reply_triage, trigger="interval", hours=3,
    id="reply_triage_every_3h", kwargs={"trigger": "cron"},
)
```

## 3. Новый метод `ThreadsWriteClient.get_replies`

```python
def get_replies(self, media_id: str) -> list[dict]:
    """GET /{media_id}/replies — ответы под собственным постом.
    Официальный API (SPEC.md §4: публикация/ответы/insights — API, не браузер)."""
```
Возвращает список сырых словарей (id, username, text, timestamp) — точная форма ответа Graph API не проверена на живых данных нигде в кодовой базе (аналогичная оговорка уже есть у `check_publishing_limit(kind="replies")` в `write_client.py`). Использует существующий `_request()` с его retry/backoff на 429 — новый код здесь ничего не переизобретает.

## 4. Поток данных

1. **Отбор постов для проверки:** опубликованные посты (`status='published'`) с непустым `threads_media_id`, опубликованные не раньше `reply_triage_lookback_days` дней назад — новый ключ в `config/settings.yaml`, дефолт `30`. Ограничивает число API-вызовов по мере роста истории постов; основная активность под постом происходит в первые дни.
2. **Забор:** для каждого поста — `write_client.get_replies(media_id)`.
3. **Дедуп:** новая `repo.reply_exists(session, threads_reply_id) -> bool` (по образцу `swipe_file_post_exists`) — уже виденные ответы пропускаются без обращения к LLM. Ответы без текста/автора пропускаются без прерывания прогона (как malformed-посты у `feed_miner`).
4. **Классификация:** роль `classifier`, промпт — новая константа `CLASSIFIER_PROMPT` в `reply_triage.py` (по образцу feed_miner-овской), просит вернуть одну из пяти меток: `question|objection|praise|spam|lead`. Ответ вне этого набора → fallback на `spam` (безопасный дефолт — не публикуется и не алертит), шаг помечается `tool_ok=False`, прогон продолжается.
5. **Действие по типу:**
   - `question`/`objection` → роль `commenter` генерирует черновик. Промпт включает `niche`/`tone_seed`/`never` из `get_knowledge_base(session)` (голосовая согласованность с постами) плюс текст ответа и текст своего поста — **не** полный 4-слойный `assemble_system_prompt` (он избыточен для короткого ответа и не упомянут в SPEC.md §6.4 для этой роли). `Reply.draft_response` = сгенерированный текст, `status='pending_approval'`.
   - `praise`/`spam` → `status='ignored'`, черновик не генерируется, алерт не шлётся.
   - `lead` → `status='new'`; создаётся строка в `leads` (`threads_username=author_username`, `source_url`=ссылка на ответ, `status='scored'`, `score`/`score_reason` не заполняются — это задача `lead_scorer` из Блока 7, которого ещё нет); немедленный `send_telegram_alert` с текстом ответа, автором и ссылкой.
6. Новая `repo.insert_reply(session, **fields) -> Reply` (по образцу `insert_swipe_file_post`) сохраняет каждую обработанную запись.

## 5. Ошибки

| Ситуация | Поведение |
|---|---|
| `get_replies` падает с признаком auth/permission-сбоя (403/401) | Системная проблема → остановка всего прогона, алерт, без ретраев (как `AuthError` у `feed_miner`) |
| `get_replies` падает локально для одного поста (после исчерпания встроенного backoff) | Пропустить этот пост, шаг помечается failed, прогон продолжается со следующими постами |
| Классификатор вернул метку вне 5 допустимых | Fallback на `spam`, шаг failed, прогон продолжается |
| `BudgetExceeded` (бюджет на classifier/commenter исчерпан) | `status='budget_stop'`, алерт |
| Любое непредвиденное исключение | `status='failed'`, алерт "unexpected error" |

## 6. Тестирование

По образцу `tests/agents/test_feed_miner.py`: fake `ThreadsWriteClient` (media_id → список ответов или исключение), fake `LLMClient`, различающий вызовы `role="classifier"` и `role="commenter"`, реальная тестовая БД через `db_session` для проверки персистентности.

Кейсы: happy path (classify+draft+persist для question), дедуп по `threads_reply_id` (LLM не вызывается повторно), malformed-ответ пропускается без abort, локальный сбой одного поста не прерывает прогон, системный/auth-сбой останавливает весь прогон + алерт, `BudgetExceeded` → `budget_stop` + алерт, generic exception → `failed` + алерт, `lead` создаёт `Reply` со `status='new'` + `Lead`-строку + алерт с текстом/автором/ссылкой, `question`/`objection` → `pending_approval` с непустым `draft_response`, `praise`/`spam` → `ignored` без черновика и без алерта, неизвестная метка классификатора → fallback `spam`.

## 7. Вне scope этого дизайна

- T5.2 (апрув и отправка ответов из Telegram) — отдельный под-проект.
- Полный 4-слойный prompt assembler для черновиков ответов — используется только `niche`/`tone_seed`/`never` из knowledge_base напрямую.
- `lead_scorer` (расчёт `score`/`score_reason` для лидов) — Блок 7, не начат.
- Изменение схемы БД — таблицы `replies` и `leads` уже существуют, новых колонок не требуется.
