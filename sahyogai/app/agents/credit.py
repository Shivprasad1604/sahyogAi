"""Agent 4 — credit. "Is this loan cheap? Can I afford it?"

NO AI IN THIS FILE. Every number here is ordinary arithmetic that an
interviewer can verify with any online EMI calculator. That is the whole
point of the project's central rule:

    The AI never calculates a number. It only decides which calculator to
    run, and how to phrase the result.
"""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import LoanOffer, Transaction, User

DISCLAIMER = (
    "This is an informational calculation, not financial advice. "
    "Confirm all figures with the lender's sanction letter before signing."
)


# ------------------------------------------------------------------ EMI


def emi(principal: float, annual_rate_pct: float, months: int) -> float:
    """Reducing-balance EMI.

                P x i x (1 + i)^n
        EMI = ---------------------
                 (1 + i)^n - 1

    Reducing balance means interest is charged on what you STILL owe, which
    shrinks each month. A "flat rate" loan charges on the ORIGINAL amount the
    whole time, so it is always more expensive than the same-numbered
    reducing rate.
    """
    if months <= 0:
        raise ValueError("months must be positive")
    if principal <= 0:
        return 0.0

    i = annual_rate_pct / 100 / 12            # monthly rate
    if i == 0:
        return principal / months

    factor = (1 + i) ** months
    return principal * i * factor / (factor - 1)


def total_interest(principal: float, annual_rate_pct: float, months: int) -> float:
    return emi(principal, annual_rate_pct, months) * months - principal


# ------------------------------------------------------------------ APR


def effective_apr(
    principal: float,
    annual_rate_pct: float,
    months: int,
    fees: float = 0.0,
) -> float:
    """The number that actually matters.

    The problem: you borrow Rs.2,00,000 at 12%. A Rs.5,000 fee is deducted,
    so you RECEIVE Rs.1,95,000 — but you pay interest as if you got
    Rs.2,00,000. Your true cost is higher than 12%. How much higher?

    There is no closed-form way to solve this, so we SEARCH for it by
    bisection: guess the middle, check if you overshot, throw away half the
    range, repeat. Like guessing a number between 1 and 100 — always guess
    the middle and you find it in 7 tries. 200 rounds gives huge precision.
    """
    monthly_payment = emi(principal, annual_rate_pct, months)
    net_disbursed = principal - fees
    if net_disbursed <= 0:
        raise ValueError("fees cannot exceed the principal")

    low, high = 1e-9, 5.0                     # monthly rate search window
    for _ in range(200):
        mid = (low + high) / 2
        # present value of the payment stream at monthly rate `mid`
        pv = monthly_payment * (1 - (1 + mid) ** -months) / mid
        if pv > net_disbursed:
            low = mid                         # rate too low, search upper half
        else:
            high = mid                        # rate too high, search lower half

    monthly_rate = (low + high) / 2
    return round(monthly_rate * 12 * 100, 2)  # back to an annual percentage


def compare_offers(offers: list[dict]) -> dict:
    """Rank real offers by TRUE cost, not by the quoted rate."""
    rows = []
    for offer in offers:
        principal = float(offer["principal"])
        rate = float(offer["annual_rate_pct"])
        months = int(offer["tenure_months"])
        fees = float(offer.get("processing_fee", 0)) + float(offer.get("insurance_fee", 0))

        monthly = emi(principal, rate, months)
        rows.append(
            {
                "lender": offer.get("lender_name", "Unknown"),
                "principal": round(principal, 2),
                "quoted_rate_pct": rate,
                "tenure_months": months,
                "fees": round(fees, 2),
                "emi": round(monthly, 2),
                "total_repayment": round(monthly * months, 2),
                "total_cost_over_principal": round(monthly * months - principal, 2),
                "effective_apr_pct": effective_apr(principal, rate, months, fees),
            }
        )

    rows.sort(key=lambda r: r["effective_apr_pct"])
    result = {"offers": rows, "disclaimer": DISCLAIMER}

    if len(rows) >= 2:
        result["cheapest"] = rows[0]["lender"]
        result["saving_vs_costliest"] = round(
            rows[-1]["total_cost_over_principal"] - rows[0]["total_cost_over_principal"], 2
        )
        result["note"] = (
            "Ranked by effective APR, which includes fees. The lowest quoted "
            "rate is not always the cheapest loan."
        )
    return result


# ------------------------------------------------------------------ FOIR


FOIR_BANDS = (
    (30.0, "Comfortable", "Well within what lenders look for."),
    (40.0, "Manageable", "Workable, but little room for a bad month."),
    (50.0, "Stretched", "At or near the ceiling most lenders use."),
)


def foir(income: float, existing_emi: float, proposed_emi: float) -> float:
    """FOIR = Fixed Obligations to Income Ratio, as a percentage.

        FOIR = (existing EMIs + this new EMI) / monthly income

    This is what a bank actually checks. Our version checks it against
    MEASURED income from transactions, not a figure the user claims.
    """
    if income <= 0:
        raise ValueError("income must be positive to compute FOIR")
    return (existing_emi + proposed_emi) / income * 100


def verdict(foir_pct: float) -> str:
    for ceiling, label, _ in FOIR_BANDS:
        if foir_pct < ceiling:
            return label
    return "Above typical approval"


def _verdict_detail(foir_pct: float) -> str:
    for ceiling, _, detail in FOIR_BANDS:
        if foir_pct < ceiling:
            return detail
    return "Above the ceiling most lenders use. Risky regardless of approval."


def affordability_check_with_income(
    income: float, proposed_emi: float, existing_emi: float = 0.0
) -> dict:
    """Pure function — no database. This is the one the unit tests hit."""
    if income <= 0:
        # A heavy-cash earner can genuinely have zero parsed credits.
        # "I don't have enough data to tell you" is a correct answer here.
        # A fabricated ratio is not.
        return {
            "verdict": "insufficient_income_data",
            "message": (
                "No income could be measured from your transactions, so an "
                "affordability ratio would be meaningless. Upload a statement "
                "that includes incoming payments, or tell us your monthly income."
            ),
            "disclaimer": DISCLAIMER,
        }

    ratio = foir(income, existing_emi, proposed_emi)
    return {
        "monthly_income": round(income, 2),
        "existing_emi": round(existing_emi, 2),
        "proposed_emi": round(proposed_emi, 2),
        "foir_pct": round(ratio, 1),
        "verdict": verdict(ratio),
        "detail": _verdict_detail(ratio),
        "headroom_to_50pct": round(max(0.0, income * 0.5 - existing_emi - proposed_emi), 2),
        "disclaimer": DISCLAIMER,
    }


def existing_emi_total(db: Session, user_id: int) -> float:
    """What the user already pays every month, measured from transactions."""
    recurring = (
        db.query(func.sum(Transaction.amount))
        .filter(
            Transaction.user_id == user_id,
            Transaction.txn_type == "debit",
            Transaction.category == "loan_repayment",
            Transaction.is_recurring.is_(True),
        )
        .scalar()
    )
    if recurring:
        months = _distinct_months(db, user_id)
        return float(recurring) / max(1, months)

    total = (
        db.query(func.sum(Transaction.amount))
        .filter(
            Transaction.user_id == user_id,
            Transaction.txn_type == "debit",
            Transaction.category == "loan_repayment",
        )
        .scalar()
    )
    months = _distinct_months(db, user_id)
    return float(total or 0.0) / max(1, months)


def _distinct_months(db: Session, user_id: int) -> int:
    dates = [d[0] for d in db.query(Transaction.date).filter(Transaction.user_id == user_id).all()]
    return len({(d.year, d.month) for d in dates}) or 1


def affordability_check(db: Session, user: User, proposed_emi: float) -> dict:
    """Database-backed wrapper. Borrows MEASURED income from savings.py."""
    from app.agents.savings import measure_income   # local import avoids a cycle

    income_info = measure_income(db, user)
    result = affordability_check_with_income(
        income=income_info["monthly_income"],
        proposed_emi=proposed_emi,
        existing_emi=existing_emi_total(db, user.id),
    )
    result["income_source"] = income_info["income_source"]
    return result


# ------------------------------------------------------------------ entry


def run(db: Session, user: User, focus: str = "compare", **kwargs) -> dict:
    """Called by the router's dispatch table."""
    if focus == "affordability":
        proposed = float(kwargs.get("proposed_emi") or 0)
        if proposed <= 0:
            return {
                "error": "no_emi_given",
                "message": "Tell me the EMI amount you are considering, and I'll check it against your measured income.",
            }
        return {"kind": "affordability", **affordability_check(db, user, proposed)}

    if focus == "emi":
        principal = float(kwargs.get("principal") or 0)
        rate = float(kwargs.get("annual_rate_pct") or 0)
        months = int(kwargs.get("tenure_months") or 0)
        if principal <= 0 or months <= 0:
            return {
                "error": "missing_loan_details",
                "message": "I need the loan amount, the interest rate and the tenure in months.",
            }
        fees = float(kwargs.get("fees") or 0)
        monthly = emi(principal, rate, months)
        return {
            "kind": "emi",
            "principal": principal,
            "annual_rate_pct": rate,
            "tenure_months": months,
            "emi": round(monthly, 2),
            "total_repayment": round(monthly * months, 2),
            "total_interest": round(monthly * months - principal, 2),
            "effective_apr_pct": effective_apr(principal, rate, months, fees),
            "fees": fees,
            "disclaimer": DISCLAIMER,
        }

    # default: compare the offers saved for this user
    saved = db.query(LoanOffer).filter(LoanOffer.user_id == user.id).all()
    if not saved:
        return {
            "kind": "compare",
            "offers": [],
            "message": "No loan offers are saved yet. Add them and I'll rank them by true cost including fees.",
            "disclaimer": DISCLAIMER,
        }
    offers = [
        {
            "lender_name": o.lender_name,
            "principal": o.principal,
            "annual_rate_pct": o.annual_rate_pct,
            "tenure_months": o.tenure_months,
            "processing_fee": o.processing_fee or 0,
            "insurance_fee": o.insurance_fee or 0,
        }
        for o in saved
    ]
    return {"kind": "compare", **compare_offers(offers)}
