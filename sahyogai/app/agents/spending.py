"""Agent 1 — spending. "Where did my money go?"

Reads past transactions, groups them, and flags months that are genuinely
unusual. No AI in this file.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Transaction, User

ESSENTIAL_CATEGORIES = {
    "rent", "utilities", "groceries", "healthcare", "education",
    "transport", "loan_repayment", "insurance",
}


def breakdown(db: Session, user_id: int, months: int = 3) -> dict:
    """A GROUP BY, nothing more exotic."""
    since = datetime.utcnow() - timedelta(days=30 * months)

    rows = (
        db.query(
            Transaction.category,
            func.sum(Transaction.amount).label("total"),
            func.count(Transaction.id).label("count"),
        )
        .filter(
            Transaction.user_id == user_id,
            Transaction.txn_type == "debit",
            Transaction.date >= since,
        )
        .group_by(Transaction.category)
        .order_by(func.sum(Transaction.amount).desc())
        .all()
    )

    total = sum(float(r.total) for r in rows) or 1.0
    categories = [
        {
            "category": r.category,
            "total": round(float(r.total), 2),
            "monthly_avg": round(float(r.total) / months, 2),
            "share_pct": round(float(r.total) / total * 100, 1),
            "transactions": int(r.count),
        }
        for r in rows
    ]
    essential = sum(c["total"] for c in categories if c["category"] in ESSENTIAL_CATEGORIES)

    return {
        "months_covered": months,
        "total_spend": round(total, 2),
        "monthly_avg_spend": round(total / months, 2),
        "essential_spend": round(essential, 2),
        "discretionary_spend": round(total - essential, 2),
        "categories": categories,
    }


def anomalies(db: Session, user_id: int, z_threshold: float = 2.0) -> list[dict]:
    """The z-score, in plain words:

        z = (this month - usual month) / typical variation

    z = 2 means "two typical wobbles above your normal" — genuinely unusual,
    not just a slightly heavy month.

    Why not an ML anomaly model? You have 6-12 monthly data points per
    category. Any model would overfit. And "you spent Rs.8,200 on food, your
    usual is Rs.4,900" is more useful to a human than a confidence score.
    """
    rows = (
        db.query(Transaction.category, Transaction.date, Transaction.amount)
        .filter(Transaction.user_id == user_id, Transaction.txn_type == "debit")
        .all()
    )

    monthly: dict[str, dict[tuple, float]] = defaultdict(lambda: defaultdict(float))
    for category, date, amount in rows:
        monthly[category][(date.year, date.month)] += float(amount)

    found = []
    for category, by_month in monthly.items():
        if len(by_month) < 3:                 # need some history to judge "usual"
            continue
        ordered = [by_month[k] for k in sorted(by_month)]
        history, latest = ordered[:-1], ordered[-1]

        mean = sum(history) / len(history)
        std = (sum((v - mean) ** 2 for v in history) / len(history)) ** 0.5
        if std == 0:
            continue

        z = (latest - mean) / std
        if z >= z_threshold:
            found.append(
                {
                    "category": category,
                    "latest_month": round(latest, 2),
                    "usual_month": round(mean, 2),
                    "excess": round(latest - mean, 2),
                    "z_score": round(z, 2),
                }
            )

    found.sort(key=lambda a: a["excess"], reverse=True)
    return found


def delivery_leakage(db: Session, user_id: int, months: int = 3) -> dict | None:
    """Turn spending into a decision.

    "You spend a lot on food" changes nothing. "41 orders in 3 months at
    Rs.384 average — halving that frees Rs.31,498 a year" changes behaviour.
    """
    since = datetime.utcnow() - timedelta(days=30 * months)

    row = (
        db.query(func.sum(Transaction.amount), func.count(Transaction.id))
        .filter(
            Transaction.user_id == user_id,
            Transaction.txn_type == "debit",
            Transaction.category.in_(["food_delivery", "quick_commerce"]),
            Transaction.date >= since,
        )
        .one()
    )
    total, orders = float(row[0] or 0), int(row[1] or 0)
    if orders == 0:
        return None

    return {
        "orders": orders,
        "total_spend": round(total, 2),
        "avg_order_value": round(total / orders, 2),
        "monthly_avg": round(total / months, 2),
        "if_halved_annual_saving": round(total / months * 12 / 2, 2),
    }


def subscriptions(db: Session, user_id: int) -> list[dict]:
    rows = (
        db.query(
            Transaction.merchant,
            func.avg(Transaction.amount).label("avg_amount"),
            func.count(Transaction.id).label("count"),
            func.max(Transaction.date).label("last_seen"),
        )
        .filter(
            Transaction.user_id == user_id,
            Transaction.txn_type == "debit",
            Transaction.is_recurring.is_(True),
        )
        .group_by(Transaction.merchant)
        .all()
    )
    out = [
        {
            "merchant": r.merchant,
            "monthly_amount": round(float(r.avg_amount), 2),
            "times_charged": int(r.count),
            "last_seen": r.last_seen.strftime("%Y-%m-%d"),
            "annual_cost": round(float(r.avg_amount) * 12, 2),
        }
        for r in rows
    ]
    out.sort(key=lambda s: s["monthly_amount"], reverse=True)
    return out


def run(db: Session, user: User, focus: str = "all", months: int = 3, **_) -> dict:
    if focus == "subscriptions":
        subs = subscriptions(db, user.id)
        return {
            "kind": "subscriptions",
            "subscriptions": subs,
            "total_monthly": round(sum(s["monthly_amount"] for s in subs), 2),
            "total_annual": round(sum(s["annual_cost"] for s in subs), 2),
        }

    if focus == "anomalies":
        return {"kind": "anomalies", "anomalies": anomalies(db, user.id)}

    result = {"kind": "spending", **breakdown(db, user.id, months)}
    result["anomalies"] = anomalies(db, user.id)
    result["subscriptions"] = subscriptions(db, user.id)
    leakage = delivery_leakage(db, user.id, months)
    if leakage:
        result["delivery_leakage"] = leakage
    return result
