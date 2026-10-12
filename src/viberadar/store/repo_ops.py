"""运维仓储：成本账本、配置版本、审计、互斥锁、LLM 缓存（PRD M9）。"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from viberadar.contracts import new_id, utcnow

from .models import AuditRow, ConfigVersionRow, CostLedgerRow, JobLockRow, LLMCacheRow


# ---------- 成本 ----------


def record_cost(
    session: Session,
    model: str,
    input_tokens: int,
    cache_input_tokens: int,
    output_tokens: int,
    price_version: str,
    cost_usd: Decimal,
    run_id: str | None = None,
    stage: str | None = None,
    estimated: bool = False,
    retry: bool = False,
) -> CostLedgerRow:
    row = CostLedgerRow(
        id=new_id("cst"),
        run_id=run_id,
        stage=stage,
        model=model,
        input_tokens=max(0, input_tokens),
        cache_input_tokens=max(0, cache_input_tokens),
        output_tokens=max(0, output_tokens),
        price_version=price_version,
        cost_usd=Decimal(cost_usd),
        estimated=estimated,
        retry=retry,
        created_at=utcnow(),
    )
    session.add(row)
    session.flush()
    return row


def _day_bounds(now: datetime) -> tuple[datetime, datetime]:
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def daily_cost(session: Session, now: datetime | None = None) -> Decimal:
    now = now or utcnow()
    start, end = _day_bounds(now)
    total = session.scalar(
        select(func.coalesce(func.sum(CostLedgerRow.cost_usd), 0)).where(
            CostLedgerRow.created_at >= start, CostLedgerRow.created_at < end
        )
    )
    return Decimal(total or 0)


def monthly_cost(session: Session, now: datetime | None = None) -> Decimal:
    now = now or utcnow()
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    total = session.scalar(
        select(func.coalesce(func.sum(CostLedgerRow.cost_usd), 0)).where(
            CostLedgerRow.created_at >= start
        )
    )
    return Decimal(total or 0)


# ---------- 配置版本 ----------


def record_config_version(
    session: Session,
    kind: str,
    version: str,
    hash_value: str,
    content: dict,
    changelog: str | None,
    operator: str,
) -> ConfigVersionRow:
    existing = session.scalar(
        select(ConfigVersionRow).where(
            ConfigVersionRow.kind == kind, ConfigVersionRow.version == version
        )
    )
    if existing is not None:
        return existing
    row = ConfigVersionRow(
        id=new_id("cfg"),
        kind=kind,
        version=version,
        hash=hash_value,
        content=content,
        changelog=changelog,
        operator=operator,
        created_at=utcnow(),
    )
    session.add(row)
    session.flush()
    return row


def latest_config_version(session: Session, kind: str) -> ConfigVersionRow | None:
    return session.scalar(
        select(ConfigVersionRow)
        .where(ConfigVersionRow.kind == kind)
        .order_by(ConfigVersionRow.created_at.desc())
        .limit(1)
    )


# ---------- 审计 ----------


def record_audit(
    session: Session,
    actor: str,
    action: str,
    target: str | None = None,
    detail: dict | None = None,
) -> AuditRow:
    row = AuditRow(
        id=new_id("aud"),
        actor=actor,
        action=action,
        target=target,
        detail=detail,
        created_at=utcnow(),
    )
    session.add(row)
    session.flush()
    return row


# ---------- 互斥锁（带租约，避免崩溃后永久阻塞） ----------


def has_lock(session: Session, lock_key: str, now: datetime | None = None) -> bool:
    now = now or utcnow()
    row = session.get(JobLockRow, lock_key)
    return row is not None and row.lease_expires_at > now


def acquire_lock(
    session: Session,
    lock_key: str,
    owner: str,
    lease_seconds: int = 900,
    now: datetime | None = None,
) -> bool:
    now = now or utcnow()
    row = session.get(JobLockRow, lock_key)
    if row is not None and row.lease_expires_at > now:
        return False
    if row is None:
        row = JobLockRow(
            lock_key=lock_key,
            owner=owner,
            acquired_at=now,
            lease_expires_at=now + timedelta(seconds=lease_seconds),
        )
        session.add(row)
    else:
        row.owner = owner
        row.acquired_at = now
        row.lease_expires_at = now + timedelta(seconds=lease_seconds)
    session.flush()
    return True


def release_lock(session: Session, lock_key: str, owner: str) -> None:
    row = session.get(JobLockRow, lock_key)
    if row is not None and row.owner == owner:
        session.delete(row)
        session.flush()


# ---------- LLM 缓存 ----------


def cache_get(session: Session, prompt_hash: str) -> LLMCacheRow | None:
    return session.get(LLMCacheRow, prompt_hash)


def cache_put(session: Session, prompt_hash: str, model: str, output: dict) -> None:
    if session.get(LLMCacheRow, prompt_hash) is not None:
        return
    session.add(
        LLMCacheRow(
            prompt_hash=prompt_hash,
            model=model,
            output_json=output,
            created_at=utcnow(),
        )
    )
    session.flush()


def hash_prompt(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
