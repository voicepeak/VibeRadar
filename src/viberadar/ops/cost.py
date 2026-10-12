"""成本与预算报告（PRD 13.1）。"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from viberadar.settings import Settings
from viberadar.store import repo_ops


def cost_report(session: Session, settings: Settings) -> dict:
    daily = repo_ops.daily_cost(session)
    monthly = repo_ops.monthly_cost(session)
    budget = settings.pricing.budget
    return {
        "daily_usd": float(daily),
        "monthly_usd": float(monthly),
        "daily_optimize_target": budget.get("daily_optimize_usd", 0.1),
        "daily_alert": budget.get("daily_alert_usd", 0.5),
        "daily_hard_cap": budget.get("daily_hard_cap_usd", 2.0),
        "monthly_hard_cap": budget.get("monthly_hard_cap_usd", 30.0),
        "within_optimize_target": daily <= Decimal(str(budget.get("daily_optimize_usd", 0.1))),
        "alert": daily >= Decimal(str(budget.get("daily_alert_usd", 0.5))),
        "hard_cap_reached": daily >= Decimal(str(budget.get("daily_hard_cap_usd", 2.0))),
    }
