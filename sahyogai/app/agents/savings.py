"""Agent 3 — savings. "How much can I actually save?"

Capacity is MEASURED, not asked. People misjudge their own spending badly.
The transactions do not.
"""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.agents.spending import ESSENTIAL_CATEGORIES
from app.models import Goal, Transaction, User

DISCLAIMER = (
    "General information, not personalised investment advice. "
    "No specific fund, stock, policy or lender is recommended."
)


def _month_count(db: Session, user_id: int) -> int:
    dates = [d[0] for d in db.query(Transaction.date).filter(Transaction.user_id == user_id).all()]
    return len({(d.year, d.month) for d in dates}) or 1


def measure_income(db: Session, user: User) -> dict:
    """Income from actual credits. Falls back to the self-reported figure,
    and SAYS which one it used."""
    months = _month_count(db, user.id)
    total = (
        db.query(func.sum(Transaction.amount))
        .filter(
            Transaction.user_id == user.id,
            Transaction.txn_type == "credit",
            Transaction.category != "loan_disbursal",
        )
        .scalar()
    )

    if total and float(total) > 0:
        return {
            "monthly_income": round(float(total) / months, 2),
            "income_source": "measured_from_transactions",
            "months_measured": months,
        }

    return {
        "monthly_income": round(float(user.monthly_income_self_reported or 0), 2),
        "income_source": "self_reported" if user.monthly_income_self_reported else "unknown",
        "months_measured": months,
    }


def income_volatility(db: Session, user_id: int) -> dict:
    """How irregular is this person's income? Drives the emergency-fund target."""
    rows = (
        db.query(Transaction.date, Transaction.amount)
        .filter(Transaction.user_id == user_id, Transaction.txn_type == "credit")
        .all()
    )
    buckets: dict[tuple, float] = defaultdict(float)
    for date, amount in rows:
        buckets[(date.year, date.month)] += float(amount)

    values = [buckets[k] for k in sorted(buckets)]
    if len(values) < 2:
        return {"is_irregular": True, "reason": "not_enough_history", "cv_pct": None}

    mean = sum(values) / len(values)
    if mean == 0:
        return {"is_irregular": True, "reason": "no_income_recorded", "cv_pct": None}

    std = (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5
    cv = std / mean * 100
    return {
        "is_irregular": cv > 20,
        "cv_pct": round(cv, 1),
        "monthly_min": round(min(values), 2),
        "monthly_max": round(max(values), 2),
        "reason": "high_month_to_month_variation" if cv > 20 else "stable",
    }


def capacity(db: Session, user: User) -> dict:
    months = _month_count(db, user.id)
    income_info = measure_income(db, user)

    spend = (
        db.query(func.sum(Transaction.amount))
        .filter(Transaction.user_id == user.id, Transaction.txn_type == "debit")
        .scalar()
    )
    essential = (
        db.query(func.sum(Transaction.amount))
        .filter(
            Transaction.user_id == user.id,
            Transaction.txn_type == "debit",
            Transaction.category.in_(list(ESSENTIAL_CATEGORIES)),
        )
        .scalar()
    )

    monthly_spend = float(spend or 0) / months
    monthly_essential = float(essential or 0) / months
    surplus = income_info["monthly_income"] - monthly_spend

    return {
        "monthly_income": income_info["monthly_income"],
        "income_source": income_info["income_source"],
        "monthly_spend": round(monthly_spend, 2),
        "monthly_essential_spend": round(monthly_essential, 2),
        "monthly_surplus": round(surplus, 2),
        "savings_rate_pct": round(
            surplus / income_info["monthly_income"] * 100, 1
        ) if income_info["monthly_income"] > 0 else None,
        "months_measured": months,
    }


def emergency_fund(db: Session, user: User) -> dict:
    """Target 3-6 months of ESSENTIAL expenses — not total spend. You can cut
    Netflix in a crisis; you cannot cut rent.

    For irregular earners we automatically use the UPPER bound, because
    income variance is exactly the risk the fund exists to absorb.
    """
    cap = capacity(db, user)
    volatility = income_volatility(db, user.id)
    months_needed = 6 if volatility["is_irregular"] else 3

    target = cap["monthly_essential_spend"] * months_needed
    surplus = cap["monthly_surplus"]

    return {
        "months_of_cover": months_needed,
        "monthly_essential_spend": cap["monthly_essential_spend"],
        "target_amount": round(target, 2),
        "reason": (
            "Income varies month to month, so the upper bound of 6 months applies."
            if volatility["is_irregular"]
            else "Income looks stable, so 3 months of cover is a reasonable target."
        ),
        "income_volatility_pct": volatility["cv_pct"],
        "months_to_build_at_current_surplus": (
            round(target / surplus, 1) if surplus > 0 else None
        ),
    }


def sip_amount(target: float, months: int, annual_return_pct: float) -> float:
    """Solve the SIP future-value formula for P, the monthly contribution.

        FV = P x [((1+i)^n - 1) / i] x (1+i)
    """
    if months <= 0:
        raise ValueError("months must be positive")
    i = annual_return_pct / 100 / 12
    if i == 0:
        return target / months
    factor = ((1 + i) ** months - 1) / i * (1 + i)
    return target / factor


def goal_plan(target: float, months: int) -> dict:
    """Solve across SEVERAL assumed returns rather than one.

    Showing the range makes the uncertainty visible instead of hiding it
    behind a single optimistic figure.
    """
    return {
        "target_amount": round(target, 2),
        "months": months,
        "monthly_required": [
            {
                "assumed_annual_return_pct": rate,
                "monthly_investment": round(sip_amount(target, months, rate), 2),
            }
            for rate in (4, 6, 8, 10, 12)
        ],
        "note": "A range of assumed returns is shown because no single return is guaranteed.",
        "disclaimer": DISCLAIMER,
    }


def allocation_framework(db: Session, user: User) -> list[dict]:
    """A priority ORDER with the user's real numbers filled in.

    Why debt before investing? If a loan charges 18% and investments return
    maybe 10%, repaying the loan is a guaranteed 18% return. Not close.
    """
    from app.agents.credit import existing_emi_total

    cap = capacity(db, user)
    fund = emergency_fund(db, user)
    debt = existing_emi_total(db, user.id)

    return [
        {"priority": 1, "step": "Cover essentials",
         "amount": cap["monthly_essential_spend"],
         "detail": f"Rs.{cap['monthly_essential_spend']:,.0f}/month measured from your transactions."},
        {"priority": 2, "step": "Clear high-cost debt",
         "amount": round(debt, 2),
         "detail": (f"Rs.{debt:,.0f}/month of loan repayment seen in your data. "
                    "Repaying an 18% loan is a guaranteed 18% return, which beats a ~10% investment.")
                   if debt > 0 else "No loan repayments detected."},
        {"priority": 3, "step": "Build emergency fund",
         "amount": fund["target_amount"],
         "detail": f"Target Rs.{fund['target_amount']:,.0f}. {fund['reason']}"},
        {"priority": 4, "step": "Basic insurance",
         "amount": None,
         "detail": f"{user.dependents or 0} dependents on record. Health cover first, then term life if anyone depends on your income."},
        {"priority": 5, "step": "Invest what's left",
         "amount": max(0.0, cap["monthly_surplus"]),
         "detail": f"Rs.{max(0.0, cap['monthly_surplus']):,.0f}/month available after current spending."},
    ]


def run(db: Session, user: User, focus: str = "capacity", **kwargs) -> dict:
    if focus == "goal":
        target = float(kwargs.get("target_amount") or 0)
        months = int(kwargs.get("months") or 0)
        if target <= 0 or months <= 0:
            saved = db.query(Goal).filter(Goal.user_id == user.id).first()
            if saved:
                target, months = saved.target_amount, saved.months
            else:
                return {
                    "error": "missing_goal",
                    "message": "Tell me the amount you want to reach and in how many months.",
                }
        return {"kind": "goal", **goal_plan(target, months)}

    if focus == "emergency_fund":
        return {"kind": "emergency_fund", **emergency_fund(db, user), "disclaimer": DISCLAIMER}

    return {
        "kind": "savings",
        **capacity(db, user),
        "emergency_fund": emergency_fund(db, user),
        "allocation_framework": allocation_framework(db, user),
        "income_volatility": income_volatility(db, user.id),
        "disclaimer": DISCLAIMER,
    }
