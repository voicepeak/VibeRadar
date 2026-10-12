"""内容报错（PRD M7/4.6）：与体验评价分开；状态 new/reviewing/corrected/rejected。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from viberadar.contracts import ContentIssueCreate, ContentIssueView, IssueStatus
from viberadar.store import repo_feedback


def create_issue(
    session: Session, payload: ContentIssueCreate
) -> ContentIssueView:
    row = repo_feedback.create_content_issue(
        session,
        edition_item_id=payload.edition_item_id,
        issue_type=payload.issue_type.value,
        note=payload.note,
    )
    return _to_view(row)


def resolve_issue(
    session: Session,
    issue_id: str,
    status: IssueStatus,
    resolution_note: str,
    operator: str,
    edition_revision_id: str | None = None,
) -> ContentIssueView:
    row = repo_feedback.resolve_content_issue(
        session,
        issue_id,
        status.value,
        resolution_note,
        operator,
        edition_revision_id,
    )
    return _to_view(row)


def _to_view(row) -> ContentIssueView:  # noqa: ANN001
    from viberadar.contracts import IssueType

    return ContentIssueView(
        id=row.id,
        edition_item_id=row.edition_item_id,
        issue_type=IssueType(row.issue_type),
        note=row.note,
        submitted_at=row.submitted_at,
        status=IssueStatus(row.status),
        resolution_note=row.resolution_note,
        edition_revision_id=row.edition_revision_id,
    )
