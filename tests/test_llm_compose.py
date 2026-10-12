"""冻结期号时的模型开场白：采用、回退与一致性校验。"""

from __future__ import annotations

from viberadar.compose import freeze_edition
from viberadar.llm import LLMResult

from .test_editions import EDITION_DATE, NOW, seed


class FakeComposeLLM:
    def __init__(self, data: dict | None = None, ok: bool = True) -> None:
        self.data = data or {}
        self.ok = ok
        self.calls: list[dict] = []

    def chat_json(self, *, prompt_name, system, payload, model, **kwargs):  # noqa: ANN003
        self.calls.append(payload)
        if not self.ok:
            return LLMResult(ok=False, model=model, error="模型不可用")
        return LLMResult(ok=True, model=model, data=self.data)


def test_freeze_uses_llm_intro_when_item_ids_match(session, temp_settings):
    p1, _ = seed(session, "A1")
    p2, _ = seed(session, "A2")
    llm = FakeComposeLLM(
        {"intro": "今天这两款很值得动手试试。", "sections": [], "item_ids": [p1.id, p2.id]}
    )
    edition = freeze_edition(session, EDITION_DATE, settings=temp_settings, now=NOW, llm=llm)
    assert edition.intro == "今天这两款很值得动手试试。"
    assert llm.calls and set(llm.calls[0]["selected_items"][0]) >= {"item_id", "section", "highlight"}


def test_freeze_falls_back_on_item_id_mismatch(session, temp_settings):
    seed(session, "B1")
    llm = FakeComposeLLM({"intro": "不该采用", "sections": [], "item_ids": ["ghost"]})
    edition = freeze_edition(session, EDITION_DATE, settings=temp_settings, now=NOW, llm=llm)
    assert edition.intro.startswith("今日")
    assert "不该采用" not in edition.intro


def test_freeze_falls_back_when_llm_fails(session, temp_settings):
    seed(session, "C1")
    llm = FakeComposeLLM(ok=False)
    edition = freeze_edition(session, EDITION_DATE, settings=temp_settings, now=NOW, llm=llm)
    assert edition.intro.startswith("今日")


def test_freeze_empty_edition_skips_llm(session, temp_settings):
    llm = FakeComposeLLM({"intro": "x", "sections": [], "item_ids": []})
    edition = freeze_edition(session, EDITION_DATE, settings=temp_settings, now=NOW, llm=llm)
    assert "暂无通过核验的推荐" in (edition.intro or "")
    assert llm.calls == []
