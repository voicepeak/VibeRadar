"""编制子包（PRD M6 前半）。"""

from .edition import (
    SelectionResult,
    build_edition_view,
    build_selection,
    create_correction,
    freeze_edition,
    group_sections,
    highlights,
    latest_published,
    publish_edition,
    recent_editions,
)

__all__ = [
    "SelectionResult",
    "build_edition_view",
    "build_selection",
    "create_correction",
    "freeze_edition",
    "group_sections",
    "highlights",
    "latest_published",
    "publish_edition",
    "recent_editions",
]
