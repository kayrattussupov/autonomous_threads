import json
from datetime import datetime, timedelta

import pytz

from src.agents.base import ReActAgent
from src.agents.style_critic import run_style_critic
from src.alerts import send_telegram_alert
from src.config import load_settings
from src.db.engine import session_scope
from src.db.repo import (
    get_active_playbook_rules,
    get_active_style,
    get_knowledge_base,
    get_recent_posts,
    get_swipe_examples,
    get_top_performers,
    increment_style_variant_posts_n,
    insert_post,
)
from src.llm.client import LLMClient
from src.prompt.assembler import assemble_system_prompt
from src.tools.web_search import verify_source, web_search

CONSTITUTION_PATH = "config/constitution.md"

TOOL_SELECTION_PROMPT = """\
Ты выбираешь следующее действие. Доступные инструменты:

- get_recent_posts() — последние 30 своих постов, чтобы не повторяться
- get_top_performers() — 5 своих лучших постов по score
- get_swipe_examples(topic) — зашедшие чужие посты в нише (topic необязателен)
- web_search(query) — только для category='news', поиск свежих фактов
- verify_source(url) — проверить, что источник реально существует (для news)
- save_draft(text, category, source_url) — сохранить готовый пост (category один из:
  utp_cta, educational, news, personal). source_url необязателен, но ОБЯЗАТЕЛЕН
  для category='news' — сначала получите и проверьте его через verify_source(url)

Отвечай СТРОГО одним JSON-объектом, без текста вокруг:
{"thought": "краткое рассуждение", "tool_name": "имя_инструмента", "tool_args": {...}}

История уже вызванных инструментов и их результатов (может быть пустой):
{history}

Когда готов сохранить пост — вызови save_draft. Не вызывай save_draft больше
одного раза подряд без учёта фидбека от предыдущего вызова (если он вернул
status="rejected", перепиши текст с учётом issues и вызови save_draft снова).
"""


class ContentAgent(ReActAgent):
    def __init__(self, llm_client: LLMClient | None = None, **kwargs):
        super().__init__(agent_name="content", **kwargs)
        self._llm_client = llm_client or LLMClient()
        self._critic_failures = 0
        self._done = False
        self._system_prompt_cache: str | None = None
        self._active_style = None

    def tools(self) -> dict:
        return {
            "get_recent_posts": self._tool_get_recent_posts,
            "get_top_performers": self._tool_get_top_performers,
            "get_swipe_examples": self._tool_get_swipe_examples,
            "web_search": lambda query: web_search(query),
            "verify_source": lambda url: verify_source(url),
            "save_draft": self._tool_save_draft,
        }

    def _tool_get_recent_posts(self):
        with session_scope() as session:
            return [p.text for p in get_recent_posts(session, n=30)]

    def _tool_get_top_performers(self):
        with session_scope() as session:
            return [p.text for p in get_top_performers(session, n=5)]

    def _tool_get_swipe_examples(self, topic: str | None = None):
        with session_scope() as session:
            return [e.text for e in get_swipe_examples(session, n=8, topic=topic)]

    def system_prompt(self) -> str:
        if self._system_prompt_cache is None:
            with open(CONSTITUTION_PATH, encoding="utf-8") as f:
                constitution = f.read()
            with session_scope() as session:
                kb = get_knowledge_base(session)
                self._active_style = get_active_style(session)
                genome = self._active_style.genome if self._active_style else "(нет активного стилевого варианта)"
                rules = [r.rule_text for r in get_active_playbook_rules(session)]
                swipe = [e.text for e in get_swipe_examples(session, n=8)]
                top = [p.text for p in get_top_performers(session, n=5)]
            self._system_prompt_cache = assemble_system_prompt(
                constitution=constitution,
                knowledge_base=kb,
                active_genome=genome,
                playbook_rules=rules,
                swipe_examples=swipe,
                top_posts=top,
            )
        return self._system_prompt_cache

    def _next_publish_slot(self) -> datetime:
        settings = load_settings()
        tz = pytz.timezone(settings["publish_timezone"])
        times = sorted(settings["publish_times"])
        now = datetime.now(tz)

        with session_scope() as session:
            from src.db.models import Post
            from sqlalchemy import select
            taken = {
                p.scheduled_at.astimezone(tz)
                for p in session.execute(
                    select(Post).where(Post.status.in_(["scheduled", "published"]))
                ).scalars().all()
                if p.scheduled_at is not None
            }

        day_offset = 0
        while True:
            candidate_day = (now + timedelta(days=day_offset)).date()
            for time_str in times:
                hour, minute = (int(x) for x in time_str.split(":"))
                candidate = tz.localize(datetime.combine(candidate_day, datetime.min.time()).replace(hour=hour, minute=minute))
                if candidate <= now:
                    continue
                if candidate not in taken:
                    return candidate
            day_offset += 1

    def _tool_save_draft(self, text: str, category: str, source_url: str | None = None):
        genome = self._active_style.genome if self._active_style else ""
        with session_scope() as session:
            recent_texts = [p.text for p in get_recent_posts(session, n=30)]

        critique = run_style_critic(
            text=text,
            category=category,
            source_url=source_url,
            genome=genome,
            recent_post_texts=recent_texts,
            llm_client=self._llm_client,
            run_id=self._run_id,
        )
        self.note_llm_usage(critique["tokens_in"], critique["tokens_out"], critique["cost_usd"])

        if critique["pass"]:
            return self._persist_post(text, category, status="scheduled", source_url=source_url)

        self._critic_failures += 1
        if self._critic_failures >= 2:
            self._persist_post(text, category, status="needs_review", source_url=source_url)
            send_telegram_alert(
                f"content_agent: пост требует ручной проверки — style_critic дважды отклонил черновик: {critique['issues']}"
            )
            self._done = True
            return {"status": "needs_review", "issues": critique["issues"]}

        return {"status": "rejected", "issues": critique["issues"]}

    def _persist_post(self, text: str, category: str, status: str, source_url: str | None = None) -> dict:
        style_variant_id = self._active_style.id if self._active_style else None
        with session_scope() as session:
            post = insert_post(
                session,
                text=text,
                category=category,
                status=status,
                source_url=source_url,
                style_variant_id=style_variant_id,
                scheduled_at=self._next_publish_slot() if status == "scheduled" else None,
                model_used=self._llm_client._config["roles"]["post_writer"]["model"] if hasattr(self._llm_client, "_config") else None,
            )
            if status == "scheduled" and style_variant_id:
                increment_style_variant_posts_n(session, style_variant_id)
            post_id = post.id
        self._done = True
        return {"status": status, "post_id": post_id}

    def decide_next_action(self, history: list[dict]) -> dict | None:
        if self._done:
            return None

        # NOTE: TOOL_SELECTION_PROMPT's example tool-call is literal JSON (curly
        # braces), so str.format() would misparse it as format placeholders.
        # Use a plain substring replace instead of .format() for the {history} slot.
        history_json = json.dumps(history, ensure_ascii=False, default=str)
        messages = [
            {"role": "system", "content": self.system_prompt()},
            {"role": "user", "content": TOOL_SELECTION_PROMPT.replace("{history}", history_json)},
        ]
        response = self._llm_client.complete(role="post_writer", messages=messages, run_id=self._run_id)
        self.note_llm_usage(response.tokens_in, response.tokens_out, response.cost_usd)

        try:
            parsed = json.loads(response.text)
            return {
                "thought": parsed.get("thought"),
                "tool_name": parsed["tool_name"],
                "tool_args": parsed.get("tool_args", {}),
            }
        except (json.JSONDecodeError, KeyError, TypeError):
            return {"thought": f"invalid tool-call JSON: {response.text[:200]!r}", "tool_name": "__parse_error__", "tool_args": {}}
