"""主理人复核 CLI（PRD M5.5）。

支持通过、淘汰（带原因）、编辑文案、锁定与撤销；操作记录版本与操作者。
初版 CLI 即可完成操作，代表候选 20 条常规复核预算 ≤15 分钟。
"""

from __future__ import annotations

import json
import sys
from datetime import date
from time import monotonic

from sqlalchemy.orm import Session

from viberadar.contracts import CuratorActionType, new_id, utcnow
from viberadar.settings import Settings
from viberadar.store import repo_pipeline
from viberadar.store.models import CandidateRow, CuratorActionRow

REJECT_REASONS = {
    "1": "不相关",
    "2": "无新意",
    "3": "重复",
    "4": "证据不足",
    "5": "不可访问",
}


def _record_action(
    session: Session,
    edition_date: date,
    operator: str,
    action: CuratorActionType | str,
    candidate: CandidateRow | None,
    reason: str | None = None,
    diff: dict | None = None,
) -> None:
    action_value = action.value if hasattr(action, "value") else str(action)
    session.add(
        CuratorActionRow(
            id=new_id("cur"),
            edition_id=edition_date.isoformat(),
            edition_item_id=candidate.id if candidate else None,
            action=action_value,
            reason=reason,
            diff=diff,
            operator=operator,
            revision=1,
            created_at=utcnow(),
        )
    )
    session.flush()


def list_candidates_payload(session: Session, edition_date: date) -> list[dict]:
    payload = []
    for candidate in repo_pipeline.list_candidates(session, edition_date):
        content = candidate.content_json or {}
        payload.append(
            {
                "product_id": candidate.product_id,
                "slug": content.get("name"),
                "name": content.get("name"),
                "category": content.get("category_label"),
                "score": float(candidate.final_score),
                "auto_selected": candidate.auto_selected,
                "curator_status": candidate.curator_status,
                "locked": candidate.locked,
                "one_liner": content.get("one_liner"),
                "access_text": content.get("access_text"),
                "mode": content.get("mode"),
                "evidence_status": content.get("evidence_status"),
                "sources": [s.get("url") for s in (content.get("source_links") or [])],
            }
        )
    return payload


def apply_decision(
    session: Session,
    edition_date: date,
    product_id: str,
    operator: str,
    decision: str,
    reason: str | None = None,
    edits: dict | None = None,
    lock: bool | None = None,
    position: int | None = None,
) -> CandidateRow:
    candidate = repo_pipeline.get_candidate(session, edition_date, product_id)
    if candidate is None:
        raise KeyError(f"候选中不存在: {product_id}")
    status_map = {
        "approve": "approved",
        "reject": "rejected",
        "undo": None,
    }
    status = status_map.get(decision, decision)
    repo_pipeline.apply_curator_change(
        session,
        candidate,
        status=status,
        reason=reason,
        edits=edits,
        locked=lock,
        position_hint=position,
    )
    _record_action(
        session,
        edition_date,
        operator,
        decision,
        candidate,
        reason=reason,
        diff=edits,
    )
    return candidate


def review_interactive(
    session: Session,
    edition_date: date,
    settings: Settings,
    operator: str = "curator",
) -> dict:
    candidates = repo_pipeline.list_candidates(session, edition_date)
    if not candidates:
        print(f"{edition_date} 没有候选。请先运行 pipeline。")
        return {"reviewed": 0}
    start = monotonic()
    index = 0
    print(
        f"复核 {edition_date}：{len(candidates)} 条候选。"
        "键位：[j]下一条 [k]上一条 [a]通过 [r]淘汰 [e]编辑 [l]锁定 [u]撤销 [s]保存退出 [q]退出"
    )
    while True:
        candidate = candidates[index]
        content = candidate.content_json or {}
        status = candidate.curator_status or ("auto" if candidate.auto_selected else "candidate")
        print(
            f"\n[{index + 1}/{len(candidates)}] ({status}) {content.get('name')} · "
            f"{content.get('category_label')}"
        )
        print(f"  一句话：{content.get('one_liner')}")
        print(f"  门槛：{content.get('access_text')} · 模式：{content.get('mode')}")
        print(f"  证据：{content.get('evidence_status')} · 分数：{float(candidate.final_score):.3f}")
        for source in content.get("source_links") or []:
            print(f"  来源[{source.get('label')}]：{source.get('url')}")
        try:
            key = input("> ").strip().lower()
        except EOFError:
            key = "s"
        if key == "j":
            index = min(len(candidates) - 1, index + 1)
        elif key == "k":
            index = max(0, index - 1)
        elif key == "a":
            apply_decision(session, edition_date, candidate.product_id, operator, "approve")
            session.commit()
            print("已通过。")
        elif key == "r":
            choice = input(
                "淘汰原因：[1]不相关 [2]无新意 [3]重复 [4]证据不足 [5]不可访问："
            ).strip()
            reason = REJECT_REASONS.get(choice, choice or "未注明")
            apply_decision(
                session, edition_date, candidate.product_id, operator, "reject", reason=reason
            )
            session.commit()
            print(f"已淘汰（{reason}）。")
        elif key == "e":
            new_one_liner = input("新的一句话（留空保持）：").strip()
            edits = {}
            if new_one_liner:
                edits["one_liner"] = new_one_liner[:40]
            if edits:
                apply_decision(
                    session, edition_date, candidate.product_id, operator, "edit", edits=edits
                )
                session.commit()
                print("已保存编辑。")
        elif key == "l":
            apply_decision(
                session,
                edition_date,
                candidate.product_id,
                operator,
                "lock",
                lock=not candidate.locked,
            )
            session.commit()
            print("锁定状态：" + ("已锁定" if candidate.locked else "已解锁"))
        elif key == "u":
            apply_decision(session, edition_date, candidate.product_id, operator, "undo")
            session.commit()
            print("已撤销人工操作。")
        elif key in ("s", "q"):
            session.commit()
            elapsed = monotonic() - start
            print(f"退出。复核用时 {elapsed/60:.1f} 分钟。")
            return {
                "reviewed": len(candidates),
                "elapsed_seconds": round(elapsed),
                "within_budget": elapsed <= 15 * 60,
            }
        else:
            print("未知键位。")


def print_candidates(session: Session, edition_date: date, as_json: bool = False) -> None:
    payload = list_candidates_payload(session, edition_date)
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if not payload:
        print(f"{edition_date} 没有候选。")
        return
    for idx, item in enumerate(payload, start=1):
        status = item["curator_status"] or ("auto" if item["auto_selected"] else "candidate")
        print(
            f"{idx:2d}. [{status}] {item['name']} · {item['category']} · {item['score']:.3f} · "
            f"{item['evidence_status']}"
        )
        print(f"    一句话：{item['one_liner']}")
        if item["sources"]:
            print(f"    来源：{item['sources'][0]}")
        print(f"    product_id：{item['product_id']}")


def _reconfigure_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
