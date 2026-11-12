"""Agent 5 — loan shield. "Is this app allowed to do this?"

Deliberately RULE-BASED. Why not ML?
  - No labelled dataset of predatory loans exists.
  - The signals are already crisply defined.
  - A user deserves to know WHY they were warned. "This matched a known
    pattern" beats "the model scored 0.87".

Every finding carries the RBI reference and the user's rights, because a
right you do not know about is not a right.
"""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy.orm import Session

from app.models import LenderPattern, Transaction, User

# Rights under RBI's Digital Lending Guidelines and the Fair Practices Code.
RBI_RIGHTS = [
    {
        "right": "No contacting your family, friends or contact list",
        "rule": "RBI Digital Lending Guidelines, 2022 — recovery agents may not harass borrowers or their contacts.",
    },
    {
        "right": "No access to your phone contacts, photos or files",
        "rule": "RBI Digital Lending Guidelines, 2022 — apps may collect only need-based data, with explicit consent.",
    },
    {
        "right": "A Key Fact Statement before you borrow",
        "rule": "RBI Digital Lending Guidelines, 2022 — the all-in annual cost (APR) must be disclosed upfront.",
    },
    {
        "right": "Recovery calls only between 8am and 7pm",
        "rule": "RBI Fair Practices Code — no calls outside these hours, no abusive or threatening language.",
    },
    {
        "right": "A cooling-off period to exit without penalty",
        "rule": "RBI Digital Lending Guidelines, 2022 — exit by repaying the principal plus proportionate APR.",
    },
    {
        "right": "Lending only by an RBI-regulated entity",
        "rule": "An app must name the RBI-regulated bank or NBFC behind it. Verify it on the RBI register.",
    },
]

COMPLAINT_CHANNELS = [
    {"name": "RBI Complaint Management System", "url": "https://cms.rbi.org.in",
     "use_for": "Complaints against a bank or an RBI-regulated NBFC."},
    {"name": "RBI Sachet", "url": "https://sachet.rbi.org.in",
     "use_for": "Reporting unregistered or unauthorised lending entities."},
    {"name": "National Cyber Crime Portal", "url": "https://cybercrime.gov.in",
     "use_for": "Harassment, blackmail, or misuse of your photos and contacts."},
    {"name": "Local police — FIR", "url": None,
     "use_for": "Threats, extortion or criminal intimidation. This is a police matter."},
]

# Apps named in RBI / press advisories, plus generic high-risk naming patterns.
DEFAULT_PATTERNS = [
    "RUPEE", "CASHBEAN", "LOANGRAM", "QUICKRUPEE", "SNAPIT", "INSTACASH",
    "FLASHRUPEE", "CASHMAMA", "RAPIDRUPEE", "TURBOLOAN", "GOLDBOWL",
]


def _known_lender_matches(db: Session, user_id: int) -> list[dict]:
    """Strategy 1 — known-name matching."""
    patterns = [p.pattern.upper() for p in db.query(LenderPattern).all()] or DEFAULT_PATTERNS

    rows = (
        db.query(Transaction.merchant, Transaction.raw_description, Transaction.amount, Transaction.date)
        .filter(Transaction.user_id == user_id)
        .all()
    )

    seen, findings = set(), []
    for merchant, description, amount, date in rows:
        haystack = f"{merchant or ''} {description or ''}".upper()
        for pattern in patterns:
            if pattern in haystack and pattern not in seen:
                seen.add(pattern)
                findings.append(
                    {
                        "type": "known_high_risk_lender",
                        "severity": "high",
                        "matched": pattern,
                        "merchant": merchant,
                        "first_seen": date.strftime("%Y-%m-%d"),
                        "why": "This name matches a lender flagged in RBI or press advisories.",
                    }
                )
    return findings


def _short_tenure_high_cost(db: Session, user_id: int) -> list[dict]:
    """Strategy 2 — the clever one. Find predatory loans WITHOUT knowing the
    lender's name, purely from the shape of the money movement:

        Day 1:  +Rs.5,000 from LENDER-X   (money in)
        Day 20: -Rs.6,000 to   LENDER-X   (money out, bigger)

    20% in 20 days annualises to roughly 365%.
    """
    rows = (
        db.query(Transaction.merchant, Transaction.date, Transaction.amount, Transaction.txn_type)
        .filter(Transaction.user_id == user_id)
        .order_by(Transaction.date)
        .all()
    )

    by_merchant: dict[str, list] = defaultdict(list)
    for merchant, date, amount, txn_type in rows:
        by_merchant[merchant or "UNKNOWN"].append((date, float(amount), txn_type))

    findings = []
    for merchant, entries in by_merchant.items():
        credits = [(d, a) for d, a, t in entries if t == "credit"]
        debits = [(d, a) for d, a, t in entries if t == "debit"]
        if not credits or not debits:
            continue

        for credit_date, credit_amount in credits:
            for debit_date, debit_amount in debits:
                days = (debit_date - credit_date).days
                if not (0 < days <= 60) or debit_amount <= credit_amount:
                    continue

                implied = (debit_amount - credit_amount) / credit_amount
                annualised = implied * (365 / days) * 100
                if annualised <= 50:
                    continue

                findings.append(
                    {
                        "type": "short_tenure_high_cost",
                        "severity": "high" if annualised > 100 else "medium",
                        "merchant": merchant,
                        "borrowed": round(credit_amount, 2),
                        "repaid": round(debit_amount, 2),
                        "days": days,
                        "implied_annual_cost_pct": round(annualised, 1),
                        "why": (
                            f"Rs.{credit_amount:,.0f} came in and Rs.{debit_amount:,.0f} went back "
                            f"out {days} days later. That is about {annualised:,.0f}% a year."
                        ),
                    }
                )
                break   # one finding per credit is enough

    findings.sort(key=lambda f: f["implied_annual_cost_pct"], reverse=True)
    return findings[:10]


def _loan_stacking(db: Session, user_id: int) -> list[dict]:
    """Strategy 3 — small loan-like credits from 3+ different sources.
    The signature of borrowing to repay earlier borrowing."""
    rows = (
        db.query(Transaction.merchant, Transaction.date, Transaction.amount)
        .filter(
            Transaction.user_id == user_id,
            Transaction.txn_type == "credit",
            Transaction.category.in_(["loan_disbursal", "other"]),
            Transaction.amount <= 50000,
        )
        .order_by(Transaction.date)
        .all()
    )
    if len(rows) < 3:
        return []

    by_month: dict[tuple, set] = defaultdict(set)
    for merchant, date, _ in rows:
        by_month[(date.year, date.month)].add(merchant)

    findings = []
    for (year, month), sources in sorted(by_month.items()):
        if len(sources) >= 3:
            findings.append(
                {
                    "type": "loan_stacking",
                    "severity": "high",
                    "month": f"{year:04d}-{month:02d}",
                    "distinct_sources": len(sources),
                    "sources": sorted(sources),
                    "why": (
                        f"{len(sources)} different lenders disbursed money to you in the same month. "
                        "Borrowing from one lender to repay another is how a debt trap starts."
                    ),
                }
            )
    return findings


def scan(db: Session, user_id: int) -> dict:
    findings = (
        _known_lender_matches(db, user_id)
        + _short_tenure_high_cost(db, user_id)
        + _loan_stacking(db, user_id)
    )
    high = sum(1 for f in findings if f["severity"] == "high")

    return {
        "kind": "loan_shield",
        "findings": findings,
        "findings_count": len(findings),
        "high_severity_count": high,
        "risk_level": "high" if high else ("medium" if findings else "low"),
        "your_rights": RBI_RIGHTS,
        "complaint_channels": COMPLAINT_CHANNELS,
        "summary": (
            f"{len(findings)} issue(s) found in your transactions."
            if findings
            else "Nothing in your transactions matches a known predatory-lending pattern. "
                 "Your rights are listed below regardless."
        ),
        "disclaimer": (
            "This is a pattern check on your transaction data, not a legal opinion. "
            "If you are being harassed, use the complaint channels listed."
        ),
    }


def run(db: Session, user: User, **_) -> dict:
    return scan(db, user.id)
