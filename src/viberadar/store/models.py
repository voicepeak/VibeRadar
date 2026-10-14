"""SQLAlchemy 模型（PRD 8.2 逻辑表 / 8.3 约束）。

时间统一以 UTC 存储（UTCDateTime），JSON 字段以 TEXT 存储。
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class UTCDateTime(TypeDecorator):
    """带时区入参；SQLite 内保存 naive UTC，读出恢复 UTC。"""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):  # noqa: ANN001
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("写入数据库的时间必须带时区（PRD 5.1）")
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect):  # noqa: ANN001
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc)


class FeedItemRow(Base):
    __tablename__ = "feed_items"
    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), unique=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("launch_events.id"))
    raw_item_id: Mapped[str] = mapped_column(ForeignKey("raw_items.id"))
    state: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    category: Mapped[str | None] = mapped_column(String(32))
    spam: Mapped[float] = mapped_column(Numeric(6, 4), default=0)
    interest: Mapped[float | None] = mapped_column(Numeric(6, 4))
    final_score: Mapped[float] = mapped_column(Numeric(6, 4), default=0)
    signals: Mapped[dict] = mapped_column(JSON, default=dict)
    content_json: Mapped[dict] = mapped_column(JSON, default=dict)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime)
    event_at: Mapped[datetime] = mapped_column(UTCDateTime)
    analyzed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    rank_updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    must_read_until: Mapped[datetime | None] = mapped_column(UTCDateTime)


class RefreshRunRow(Base):
    __tablename__ = "refresh_runs"
    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    trigger: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(24), default="running")
    since: Mapped[datetime] = mapped_column(UTCDateTime)
    until: Mapped[datetime] = mapped_column(UTCDateTime)
    new_raw: Mapped[int] = mapped_column(Integer, default=0)
    new_items: Mapped[int] = mapped_column(Integer, default=0)
    llm_calls: Mapped[int] = mapped_column(Integer, default=0)
    budget_exhausted: Mapped[bool] = mapped_column(Boolean, default=False)
    delivery: Mapped[str] = mapped_column(String(40), default="pending(TODO feishu)")
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class TryRecipeRow(Base):
    __tablename__ = "try_recipes"
    __table_args__ = (UniqueConstraint("slug", "version"),)
    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    slug: Mapped[str] = mapped_column(String(160), index=True)
    version: Mapped[str] = mapped_column(String(64))
    content_json: Mapped[dict] = mapped_column(JSON)
    verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    last_success_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class TrialSessionRow(Base):
    __tablename__ = "trial_sessions"
    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    recipe_id: Mapped[str] = mapped_column(ForeignKey("try_recipes.id"))
    product_id: Mapped[str | None] = mapped_column(ForeignKey("products.id"))
    edition_item_id: Mapped[str | None] = mapped_column(ForeignKey("edition_items.id"))
    actor_hash: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="preparing")
    key_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    budget_usd: Mapped[Decimal] = mapped_column(Numeric(10, 6), default=Decimal("0.10"))
    spent_usd: Mapped[Decimal] = mapped_column(Numeric(10, 6), default=Decimal("0"))
    url: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime)
    ready_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    error: Mapped[str | None] = mapped_column(Text)


class TrialEventRow(Base):
    __tablename__ = "trial_events"
    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("trial_sessions.id"), index=True)
    kind: Mapped[str] = mapped_column(String(24))
    message: Mapped[str] = mapped_column(Text)
    actor_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class Source(Base):
    __tablename__ = "sources"

    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    label: Mapped[str] = mapped_column(String(128))
    tier: Mapped[int] = mapped_column(Integer, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    config_version: Mapped[str | None] = mapped_column(String(64))
    last_success_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    watermark: Mapped[dict | None] = mapped_column(JSON)
    notes: Mapped[str | None] = mapped_column(Text)


class RawItemRow(Base):
    __tablename__ = "raw_items"
    __table_args__ = (
        UniqueConstraint("source_key", "external_id", name="uq_raw_items_source_external"),
        Index("ix_raw_items_published_at", "published_at"),
        Index("ix_raw_items_first_fetched_at", "first_fetched_at"),
    )

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    source_key: Mapped[str] = mapped_column(String(32), ForeignKey("sources.key"))
    external_id: Mapped[str] = mapped_column(String(256))
    title: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(Text)
    target_url: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(String(256))
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    first_fetched_at: Mapped[datetime] = mapped_column(UTCDateTime)
    last_fetched_at: Mapped[datetime] = mapped_column(UTCDateTime)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    metadata_json: Mapped[dict | None] = mapped_column(JSON)
    query_key: Mapped[str | None] = mapped_column(String(64))


class MetricSnapshotRow(Base):
    __tablename__ = "metric_snapshots"
    __table_args__ = (Index("ix_metric_raw_time", "raw_item_id", "captured_at"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    raw_item_id: Mapped[str] = mapped_column(
        String(48), ForeignKey("raw_items.id", ondelete="CASCADE")
    )
    captured_at: Mapped[datetime] = mapped_column(UTCDateTime)
    metrics: Mapped[dict] = mapped_column(JSON)


class ProductRow(Base):
    __tablename__ = "products"
    __table_args__ = (Index("ix_products_status", "status"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    slug: Mapped[str] = mapped_column(String(160), unique=True)
    name: Mapped[str] = mapped_column(String(256))
    canonical_url: Mapped[str | None] = mapped_column(Text)
    repo_id: Mapped[str | None] = mapped_column(String(128), index=True)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime)
    category: Mapped[str | None] = mapped_column(String(32))
    summary: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="active")
    merged_into_id: Mapped[str | None] = mapped_column(String(48), ForeignKey("products.id"))
    retraction_note: Mapped[str | None] = mapped_column(Text)
    canonical_key: Mapped[str | None] = mapped_column(String(256), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)


class LaunchEventRow(Base):
    __tablename__ = "launch_events"
    __table_args__ = (
        UniqueConstraint("product_id", "event_fingerprint", name="uq_launch_fingerprint"),
        Index("ix_launch_events_event_at", "event_at"),
    )

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    product_id: Mapped[str] = mapped_column(String(48), ForeignKey("products.id"))
    kind: Mapped[str] = mapped_column(String(16))
    event_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime)
    event_fingerprint: Mapped[str] = mapped_column(String(128))
    late_discovery: Mapped[bool] = mapped_column(Boolean, default=False)
    time_unconfirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class ProductSourceRow(Base):
    __tablename__ = "product_sources"
    __table_args__ = (
        UniqueConstraint("raw_item_id", name="uq_product_sources_raw_item"),
    )

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    product_id: Mapped[str] = mapped_column(String(48), ForeignKey("products.id"))
    raw_item_id: Mapped[str] = mapped_column(String(48), ForeignKey("raw_items.id"))
    match_method: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[float] = mapped_column(Numeric(4, 3), default=1.0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class EventSourceRow(Base):
    __tablename__ = "event_sources"

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    launch_event_id: Mapped[str] = mapped_column(String(48), ForeignKey("launch_events.id"))
    raw_item_id: Mapped[str] = mapped_column(String(48), ForeignKey("raw_items.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class EvidenceRow(Base):
    __tablename__ = "evidence"

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    raw_item_id: Mapped[str | None] = mapped_column(String(48), ForeignKey("raw_items.id"))
    source_url: Mapped[str] = mapped_column(Text)
    excerpt: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    retrieved_at: Mapped[datetime] = mapped_column(UTCDateTime)
    evidence_kind: Mapped[str] = mapped_column(String(24), default="excerpt")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class EnrichmentRow(Base):
    __tablename__ = "enrichments"

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    product_id: Mapped[str] = mapped_column(String(48), ForeignKey("products.id"))
    event_id: Mapped[str | None] = mapped_column(String(48), ForeignKey("launch_events.id"))
    access_json: Mapped[dict] = mapped_column(JSON)
    try_guide_json: Mapped[dict] = mapped_column(JSON)
    verification: Mapped[str] = mapped_column(String(24))
    claim_refs: Mapped[list | None] = mapped_column(JSON)
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    model_version: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class MergeActionRow(Base):
    __tablename__ = "entity_merge_actions"

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    merged_from_product_id: Mapped[str] = mapped_column(String(48))
    merged_into_product_id: Mapped[str] = mapped_column(String(48))
    relations_snapshot: Mapped[dict] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(Text)
    rule_version: Mapped[str] = mapped_column(String(32))
    operator: Mapped[str] = mapped_column(String(64))
    undone: Mapped[bool] = mapped_column(Boolean, default=False)
    undone_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class PendingRelationRow(Base):
    """待复核关系：完全同名或语义相似（PRD M2）。"""

    __tablename__ = "pending_relations"

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    product_id_a: Mapped[str] = mapped_column(String(48), ForeignKey("products.id"))
    product_id_b: Mapped[str] = mapped_column(String(48), ForeignKey("products.id"))
    relation_type: Mapped[str] = mapped_column(String(32))
    signals: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="open")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class PipelineRunRow(Base):
    __tablename__ = "pipeline_runs"

    run_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    edition_id: Mapped[str | None] = mapped_column(String(48))
    window_start: Mapped[datetime] = mapped_column(UTCDateTime)
    window_end: Mapped[datetime] = mapped_column(UTCDateTime)
    cutoff: Mapped[datetime] = mapped_column(UTCDateTime)
    status: Mapped[str] = mapped_column(String(16), default="success")
    code_version: Mapped[str] = mapped_column(String(32))
    config_hash: Mapped[str] = mapped_column(String(32))
    prompt_hash: Mapped[str | None] = mapped_column(String(32))
    model_ids: Mapped[list | None] = mapped_column(JSON)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    replay_of: Mapped[str | None] = mapped_column(String(48))
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)


class StageRunRow(Base):
    __tablename__ = "stage_runs"
    __table_args__ = (Index("ix_stage_runs_run", "run_id", "stage"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(48), ForeignKey("pipeline_runs.run_id"))
    stage: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))
    input_count: Mapped[int] = mapped_column(Integer, default=0)
    output_count: Mapped[int] = mapped_column(Integer, default=0)
    error_ids: Mapped[list | None] = mapped_column(JSON)
    artifact_ids: Mapped[list | None] = mapped_column(JSON)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 6), default=Decimal("0"))
    cache_key: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class EditionRow(Base):
    __tablename__ = "editions"
    __table_args__ = (
        UniqueConstraint("date", "timezone", name="uq_editions_date_tz"),
        Index("ix_editions_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    date: Mapped[date] = mapped_column(Date)
    timezone: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="draft")
    content_status: Mapped[str] = mapped_column(String(16), default="normal")
    mode: Mapped[str] = mapped_column(String(16), default="auto")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    config_version: Mapped[str | None] = mapped_column(String(32))
    run_id: Mapped[str | None] = mapped_column(String(48))
    intro: Mapped[str | None] = mapped_column(Text)
    source_coverage: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)


class EditionItemRow(Base):
    __tablename__ = "edition_items"
    __table_args__ = (
        UniqueConstraint("edition_id", "revision", "position", name="uq_edition_item_pos"),
        UniqueConstraint("edition_id", "revision", "product_id", name="uq_edition_item_product"),
        Index("ix_edition_items_edition", "edition_id", "revision"),
    )

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    edition_id: Mapped[str] = mapped_column(String(48), ForeignKey("editions.id"))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    position: Mapped[int] = mapped_column(Integer)
    section: Mapped[str] = mapped_column(String(16), default="tool")
    product_id: Mapped[str] = mapped_column(String(48), ForeignKey("products.id"))
    event_id: Mapped[str | None] = mapped_column(String(48), ForeignKey("launch_events.id"))
    final_score: Mapped[float] = mapped_column(Numeric(6, 4), default=0)
    signals: Mapped[dict | None] = mapped_column(JSON)
    content_json: Mapped[dict] = mapped_column(JSON)
    highlight: Mapped[bool] = mapped_column(Boolean, default=False)
    revision_note: Mapped[str | None] = mapped_column(Text)
    retracted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class OutboundLinkRow(Base):
    __tablename__ = "outbound_links"

    link_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    edition_item_id: Mapped[str | None] = mapped_column(String(48), ForeignKey("edition_items.id"))
    target_url: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class DeliveryAttemptRow(Base):
    __tablename__ = "delivery_attempts"
    __table_args__ = (
        UniqueConstraint(
            "edition_id", "channel", "destination", "dispatch_version",
            name="uq_delivery_attempt",
        ),
        Index("ix_delivery_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    edition_id: Mapped[str] = mapped_column(String(48), ForeignKey("editions.id"))
    channel: Mapped[str] = mapped_column(String(16))
    destination: Mapped[str] = mapped_column(String(128))
    dispatch_version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    external_message_id: Mapped[str | None] = mapped_column(String(128))
    request_hash: Mapped[str | None] = mapped_column(String(64))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)


class DeliveryLogRow(Base):
    """append-only 请求明细（PRD 8.3）。"""

    __tablename__ = "delivery_logs"

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    delivery_attempt_id: Mapped[str] = mapped_column(
        String(48), ForeignKey("delivery_attempts.id")
    )
    request_hash: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24))
    http_status: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class EventRow(Base):
    __tablename__ = "events"
    __table_args__ = (Index("ix_events_server_at", "server_at"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    client_event_id: Mapped[str] = mapped_column(String(128), unique=True)
    actor_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    edition_id: Mapped[str | None] = mapped_column(String(48))
    edition_item_id: Mapped[str | None] = mapped_column(String(48))
    event_type: Mapped[str] = mapped_column(String(24))
    value: Mapped[dict | None] = mapped_column(JSON)
    ref: Mapped[str] = mapped_column(String(16))
    server_at: Mapped[datetime] = mapped_column(UTCDateTime)
    identity_available: Mapped[bool] = mapped_column(Boolean, default=True)


class FeedbackStateRow(Base):
    __tablename__ = "feedback_states"
    __table_args__ = (
        UniqueConstraint("actor_hash", "edition_item_id", name="uq_feedback_state"),
    )

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    actor_hash: Mapped[str] = mapped_column(String(64))
    edition_item_id: Mapped[str] = mapped_column(String(48), ForeignKey("edition_items.id"))
    tried: Mapped[bool | None] = mapped_column(Boolean)
    rating_state: Mapped[str | None] = mapped_column(String(16))
    failure_reason: Mapped[str | None] = mapped_column(String(32))
    failure_note: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)


class FeedbackAuditRow(Base):
    """反馈操作历史（审计用；统计只看当前态）。"""

    __tablename__ = "feedback_audit"

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    actor_hash: Mapped[str] = mapped_column(String(64))
    edition_item_id: Mapped[str] = mapped_column(String(48))
    action_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class ContentIssueRow(Base):
    __tablename__ = "content_issues"
    __table_args__ = (Index("ix_issues_status", "status"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    edition_item_id: Mapped[str] = mapped_column(String(48), ForeignKey("edition_items.id"))
    issue_type: Mapped[str] = mapped_column(String(24))
    note: Mapped[str] = mapped_column(Text, default="")
    submitted_at: Mapped[datetime] = mapped_column(UTCDateTime)
    status: Mapped[str] = mapped_column(String(16), default="new")
    resolution_note: Mapped[str | None] = mapped_column(Text)
    edition_revision_id: Mapped[str | None] = mapped_column(String(48))
    operator: Mapped[str | None] = mapped_column(String(64))
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class CuratorActionRow(Base):
    __tablename__ = "curator_actions"
    __table_args__ = (Index("ix_curator_edition", "edition_id"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    edition_id: Mapped[str] = mapped_column(String(48))
    edition_item_id: Mapped[str | None] = mapped_column(String(48))
    action: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column(Text)
    diff: Mapped[dict | None] = mapped_column(JSON)
    operator: Mapped[str] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class SlugRedirectRow(Base):
    """合并后旧 slug 301 映射（PRD 4.6/M2）。"""

    __tablename__ = "slug_redirects"

    old_slug: Mapped[str] = mapped_column(String(160), primary_key=True)
    product_id: Mapped[str] = mapped_column(String(48), ForeignKey("products.id"))
    merge_action_id: Mapped[str | None] = mapped_column(String(48))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class ClassificationRow(Base):
    __tablename__ = "classifications"
    __table_args__ = (
        UniqueConstraint("edition_date", "product_id", name="uq_classification_date_product"),
        Index("ix_classifications_date", "edition_date"),
    )

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    edition_date: Mapped[date] = mapped_column(Date)
    product_id: Mapped[str] = mapped_column(String(48), ForeignKey("products.id"))
    event_id: Mapped[str | None] = mapped_column(String(48))
    raw_item_id: Mapped[str | None] = mapped_column(String(48))
    relevant: Mapped[bool | None] = mapped_column(Boolean)
    category: Mapped[str | None] = mapped_column(String(32))
    confidence: Mapped[float] = mapped_column(Numeric(4, 3))
    spam_score: Mapped[float] = mapped_column(Numeric(4, 3))
    reason: Mapped[str] = mapped_column(Text, default="")
    evidence_ids: Mapped[list | None] = mapped_column(JSON)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    model_confidence: Mapped[float | None] = mapped_column(Numeric(4, 3))
    source: Mapped[str] = mapped_column(String(16), default="llm")  # llm/rule/human
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)


class CandidateRow(Base):
    """当期候选（分类/富化/排序产物；复核层读取）。"""

    __tablename__ = "candidates"
    __table_args__ = (
        UniqueConstraint("edition_date", "product_id", name="uq_candidate_date_product"),
        Index("ix_candidates_date", "edition_date"),
    )

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    edition_date: Mapped[date] = mapped_column(Date)
    run_id: Mapped[str] = mapped_column(String(48))
    product_id: Mapped[str] = mapped_column(String(48), ForeignKey("products.id"))
    event_id: Mapped[str | None] = mapped_column(String(48))
    status: Mapped[str] = mapped_column(String(16), default="candidate")
    auto_selected: Mapped[bool] = mapped_column(Boolean, default=False)
    curator_status: Mapped[str | None] = mapped_column(String(16))
    curator_edits: Mapped[dict | None] = mapped_column(JSON)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    position_hint: Mapped[int | None] = mapped_column(Integer)
    final_score: Mapped[float] = mapped_column(Numeric(6, 4), default=0)
    signals: Mapped[dict | None] = mapped_column(JSON)
    content_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)


class ConfigVersionRow(Base):
    __tablename__ = "config_versions"
    __table_args__ = (UniqueConstraint("kind", "version", name="uq_config_kind_version"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    version: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(32))
    content: Mapped[dict] = mapped_column(JSON)
    changelog: Mapped[str | None] = mapped_column(Text)
    operator: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class CostLedgerRow(Base):
    __tablename__ = "cost_ledger"
    __table_args__ = (
        CheckConstraint("input_tokens >= 0", name="ck_cost_input_non_negative"),
        CheckConstraint("output_tokens >= 0", name="ck_cost_output_non_negative"),
        CheckConstraint("cost_usd >= 0", name="ck_cost_non_negative"),
        Index("ix_cost_created", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    run_id: Mapped[str | None] = mapped_column(String(48))
    stage: Mapped[str | None] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    price_version: Mapped[str] = mapped_column(String(64))
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 6))
    estimated: Mapped[bool] = mapped_column(Boolean, default=False)
    retry: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class EditionRevisionRow(Base):
    __tablename__ = "edition_revisions"
    __table_args__ = (
        UniqueConstraint("edition_id", "revision", name="uq_edition_revision"),
    )

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    edition_id: Mapped[str] = mapped_column(String(48), ForeignKey("editions.id"))
    revision: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text)
    field_diff: Mapped[dict] = mapped_column(JSON)
    notify_again: Mapped[bool] = mapped_column(Boolean, default=False)
    corrected_at: Mapped[datetime] = mapped_column(UTCDateTime)


class LLMCacheRow(Base):
    __tablename__ = "llm_cache"

    prompt_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    model: Mapped[str] = mapped_column(String(64))
    output_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class JobLockRow(Base):
    """按日期的流水线互斥锁（PRD 5.4；带租约过期恢复）。"""

    __tablename__ = "job_locks"

    lock_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    owner: Mapped[str] = mapped_column(String(64))
    acquired_at: Mapped[datetime] = mapped_column(UTCDateTime)
    lease_expires_at: Mapped[datetime] = mapped_column(UTCDateTime)


class AuditRow(Base):
    """管理员审计记录（保留 1 年）。"""

    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str | None] = mapped_column(String(128))
    detail: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


# 关系（可选，用于联表读取）
from sqlalchemy.orm import relationship as _rel  # noqa: E402

ProductRow.sources = _rel(ProductSourceRow, backref="product")  # type: ignore[attr-defined]
EditionRow.items = _rel(EditionItemRow, order_by="EditionItemRow.position")  # type: ignore[attr-defined]
