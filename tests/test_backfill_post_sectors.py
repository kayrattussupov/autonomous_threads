from scripts.backfill_post_sectors import backfill_post_sectors
from src.db.models import Post
from src.llm.client import LLMResponse


class _FakeLLM:
    def __init__(self, answers: list[str]):
        self._answers = list(answers)
        self.roles = []

    def complete(self, role, messages, run_id=None, step_no=None):
        self.roles.append(role)
        return LLMResponse(text=self._answers.pop(0), tokens_in=1, tokens_out=1, cost_usd=0.0, model="kimi-k2.6", finish_reason="stop")


def test_backfill_sets_valid_sector_skips_invalid_and_leaves_tagged_posts(db_session):
    workshop = Post(text="цех и сменные наряды", category="utp_cta", status="published")
    unclear = Post(text="непонятный пост", category="utp_cta", status="published")
    tagged = Post(text="уже размечен", category="utp_cta", status="published", sector="horeca")
    db_session.add_all([workshop, unclear, tagged])
    db_session.commit()
    llm = _FakeLLM(["«Производство».", "космос"])

    result = backfill_post_sectors(db_session, llm, ["производство", "horeca"])
    db_session.commit()

    assert result["updated"] == 1
    assert result["skipped"] == [(unclear.id, "космос")]
    assert llm.roles == ["classifier", "classifier"]
    db_session.refresh(workshop)
    db_session.refresh(unclear)
    db_session.refresh(tagged)
    assert workshop.sector == "производство"
    assert unclear.sector is None
    assert tagged.sector == "horeca"
