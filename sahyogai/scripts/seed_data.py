"""Create a realistic demo user so the project runs immediately.

    python -m scripts.seed_data

Builds Ravi Kumar: a 29-year-old Bengaluru delivery rider with irregular
income, a subscription he forgot about, a predatory short-tenure loan, and
loan stacking in one month — so every agent has something real to find.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from app.database import SessionLocal, init_db
from app.models import (
    ConversationLog,
    Goal,
    LenderPattern,
    LoanOffer,
    SchemeRule,
    Transaction,
    User,
)

random.seed(42)   # same demo data every run

PHONE = "+919876543210"


# --------------------------------------------------------------- schemes

SCHEMES = [
    dict(
        scheme_name="Pradhan Mantri Shram Yogi Maandhan (PM-SYM)",
        scheme_code="PM_SYM",
        benefit_summary="Rs.3,000 monthly pension after age 60 for unorganised-sector workers. The government matches your contribution rupee for rupee.",
        apply_url="https://maandhan.in",
        min_age=18, max_age=40, min_income=None, max_income=180000,
        applicable_state=None, applicable_occupation=None,
    ),
    dict(
        scheme_name="Atal Pension Yojana (APY)",
        scheme_code="APY",
        benefit_summary="A guaranteed pension of Rs.1,000 to Rs.5,000 a month from age 60, depending on your contribution.",
        apply_url="https://npscra.nsdl.co.in",
        min_age=18, max_age=40, min_income=None, max_income=None,
        applicable_state=None, applicable_occupation=None,
    ),
    dict(
        scheme_name="Indira Gandhi National Old Age Pension Scheme",
        scheme_code="IGNOAPS",
        benefit_summary="A monthly pension for citizens aged 60 and above living below the poverty line.",
        apply_url="https://nsap.nic.in",
        # The classic trap: nearly identical TEXT to the schemes above,
        # opposite AGE rule. SQL excludes it for a 29-year-old; an embedding
        # could not.
        min_age=60, max_age=None, min_income=None, max_income=100000,
        applicable_state=None, applicable_occupation=None,
    ),
    dict(
        scheme_name="Pradhan Mantri Suraksha Bima Yojana (PMSBY)",
        scheme_code="PMSBY",
        benefit_summary="Accident insurance of Rs.2 lakh for a premium of Rs.20 a year, auto-debited from your bank account.",
        apply_url="https://jansuraksha.gov.in",
        min_age=18, max_age=70, min_income=None, max_income=None,
        applicable_state=None, applicable_occupation=None,
    ),
    dict(
        scheme_name="Pradhan Mantri Jeevan Jyoti Bima Yojana (PMJJBY)",
        scheme_code="PMJJBY",
        benefit_summary="Life cover of Rs.2 lakh for a premium of Rs.436 a year.",
        apply_url="https://jansuraksha.gov.in",
        min_age=18, max_age=50, min_income=None, max_income=None,
        applicable_state=None, applicable_occupation=None,
    ),
    dict(
        scheme_name="Karnataka Gig Workers Welfare Fund",
        scheme_code="KA_GIG",
        benefit_summary="Accident cover, education support for children, and a maternity benefit for registered platform-based gig workers in Karnataka.",
        apply_url="https://karmikaspandana.karnataka.gov.in",
        min_age=18, max_age=60, min_income=None, max_income=600000,
        applicable_state="Karnataka", applicable_occupation="gig_worker",
    ),
    dict(
        scheme_name="Maharashtra Unorganised Workers Welfare Board Scheme",
        scheme_code="MH_UNORG",
        benefit_summary="Health and accident support for registered unorganised workers in Maharashtra.",
        apply_url="https://mahakamgar.maharashtra.gov.in",
        # A wrong-state scheme. SQL excludes it; the vector metadata filter
        # would also exclude it before similarity ranking.
        min_age=18, max_age=60, min_income=None, max_income=None,
        applicable_state="Maharashtra", applicable_occupation=None,
    ),
    dict(
        scheme_name="e-Shram Registration",
        scheme_code="ESHRAM",
        benefit_summary="A national database card for unorganised workers. Required to access most central welfare schemes, and carries Rs.2 lakh accident cover.",
        apply_url="https://eshram.gov.in",
        min_age=16, max_age=59, min_income=None, max_income=None,
        applicable_state=None, applicable_occupation=None,
    ),
]

SCHEME_PROSE = {
    "PM_SYM": """PM-SYM is a voluntary pension scheme for workers in the unorganised sector.

Under this scheme the subscriber receives an assured monthly pension of Rs.3,000 after attaining the age of 60 years. The Central Government contributes an equal matching share to the subscriber's contribution.

The monthly contribution depends on the age at which you join. A person joining at 29 years contributes a smaller monthly amount than someone joining at 39.

How to apply: visit a nearby Common Service Centre with your Aadhaar card and a savings bank account or Jan Dhan account passbook. Registration is free at CSCs. You can also self-enrol on the maandhan.in portal.

Documents required: Aadhaar card, savings bank account number with IFSC code, and a working mobile number.

On the death of the subscriber, the spouse is entitled to receive fifty per cent of the pension as a family pension.""",

    "APY": """Atal Pension Yojana provides a guaranteed minimum pension to subscribers in the unorganised sector.

Subscribers receive a guaranteed minimum pension of Rs.1,000, Rs.2,000, Rs.3,000, Rs.4,000 or Rs.5,000 per month after the age of 60, depending on the contribution they choose.

How to apply: approach the bank branch or post office where you hold a savings account, fill in the APY registration form, and provide your Aadhaar and mobile number. Contributions are auto-debited monthly.

The same pension amount is payable to the spouse after the subscriber's death, and the accumulated corpus is returned to the nominee thereafter.""",

    "KA_GIG": """The Karnataka Gig Workers Welfare Fund supports platform-based gig workers registered in the state.

Benefits include accident insurance cover, educational assistance for the worker's children, and a maternity benefit for women workers.

Eligibility is limited to gig workers engaged with aggregator platforms operating in Karnataka who register with the state welfare board.

How to apply: register through the Karmika Spandana portal with your Aadhaar, a Karnataka address proof, and proof of platform engagement such as an aggregator ID or earnings statement.""",

    "PMSBY": """Pradhan Mantri Suraksha Bima Yojana is an accident insurance scheme offering a cover of Rs.2 lakh.

The annual premium of Rs.20 is auto-debited from the subscriber's bank account in one instalment, usually in May or June each year.

The cover pays Rs.2 lakh for accidental death or total permanent disability, and Rs.1 lakh for partial permanent disability.

How to apply: give consent at your bank branch or through net banking. The policy renews automatically each year as long as the account has sufficient balance.""",

    "ESHRAM": """e-Shram is the national database of unorganised workers in India.

Registration provides a Universal Account Number card and includes accidental insurance cover of Rs.2 lakh under PMSBY for the first year at no cost.

Registration on e-Shram is increasingly required to access other central and state welfare schemes for unorganised workers.

How to apply: register free at eshram.gov.in or at any Common Service Centre, using your Aadhaar-linked mobile number and bank account details.""",
}


LENDER_PATTERNS = [
    ("RUPEEGO", "Named in RBI advisories on unauthorised digital lending apps."),
    ("CASHBEAN", "Reported for recovery harassment and contact-list access."),
    ("QUICKRUPEE", "Short-tenure, very high-cost lending pattern."),
    ("FLASHRUPEE", "Reported for abusive recovery practices."),
    ("INSTACASH", "Unregistered lending entity pattern."),
    ("TURBOLOAN", "Short-tenure, very high-cost lending pattern."),
]


# ---------------------------------------------------------- transactions


def _build_transactions(user_id: int) -> list[Transaction]:
    today = datetime.utcnow().replace(hour=12, minute=0, second=0, microsecond=0)
    start = (today - timedelta(days=240)).replace(day=1)

    txns: list[Transaction] = []

    def add(days_offset: int, amount: float, txn_type: str, merchant: str,
            description: str, category: str, source: str = "bank",
            recurring: bool = False):
        txns.append(
            Transaction(
                user_id=user_id,
                date=start + timedelta(days=days_offset),
                amount=round(amount, 2),
                txn_type=txn_type,
                merchant=merchant,
                raw_description=description,
                category=category,
                source=source,
                is_recurring=recurring,
            )
        )

    # 8 months of history
    for month in range(8):
        base = month * 30

        # --- income: irregular, which is the whole point of this user ---
        gig_income = random.choice([27800, 31200, 36400, 29500, 33100, 38200, 28900, 34600])
        for week in range(4):
            add(base + week * 7 + 2, gig_income / 4, "credit", "SWIGGY PAYOUT",
                "UPI/SWIGGY DELIVERY PAYOUT/WEEKLY", "income")

        # --- essentials ---
        add(base + 1, 9500, "debit", "LANDLORD RENT", "UPI/LANDLORD RENT/MONTHLY", "rent", recurring=True)
        add(base + 5, random.uniform(850, 1250), "debit", "BESCOM", "BESCOM ELECTRICITY BILL", "utilities")
        add(base + 6, 299, "debit", "JIO", "UPI/JIO RECHARGE/PREPAID", "utilities", recurring=True)
        add(base + 8, random.uniform(3200, 4100), "debit", "DMART", "POS/DMART BANGALORE", "groceries")
        add(base + 20, random.uniform(2600, 3400), "debit", "KIRANA STORE", "UPI/KIRANA STORE/PAY", "groceries")

        # --- transport ---
        for day in (3, 11, 19, 26):
            add(base + day, random.uniform(380, 620), "debit", "PETROL", "POS/HP PETROL PUMP", "transport")

        # --- the subscription he forgot about (stable amount, monthly gap) ---
        add(base + 14, 199, "debit", "NETFLIX", "UPI/NETFLIX INDIA/SUBSCRIPTION", "subscriptions", recurring=True)
        add(base + 16, 119, "debit", "SPOTIFY", "UPI/SPOTIFY INDIA/SUBSCRIPTION", "subscriptions", recurring=True)

        # --- EMI on a two-wheeler ---
        add(base + 7, 4820, "debit", "BAJAJ FIN", "ACH/BAJAJ FINANCE EMI/TWO WHEELER", "loan_repayment", recurring=True)

        # --- food delivery: the leakage, heavier in recent months ---
        orders = 9 if month < 5 else 16
        for _ in range(orders):
            day = random.randint(1, 28)
            add(base + day, random.uniform(240, 520), "debit", "SWIGGY",
                "UPI/SWIGGY BANGALORE/ORDER", "food_delivery", source="swiggy")

        # --- quick commerce ---
        for _ in range(6):
            day = random.randint(1, 28)
            add(base + day, random.uniform(180, 420), "debit", "ZEPTO",
                "UPI/ZEPTO MARKETPLACE/ORDER", "quick_commerce")

    # --- the predatory loan: Rs.5,000 in, Rs.6,000 out 20 days later ---
    #     20% in 20 days  ->  roughly 365% a year
    add(190, 5000, "credit", "QUICKRUPEE", "UPI/QUICKRUPEE DISBURSAL/LOAN", "loan_disbursal")
    add(210, 6000, "debit", "QUICKRUPEE", "UPI/QUICKRUPEE REPAYMENT/LOAN", "loan_repayment")

    # --- loan stacking: 3 different lenders in the same month ---
    add(215, 8000, "credit", "FLASHRUPEE", "UPI/FLASHRUPEE DISBURSAL/LOAN", "loan_disbursal")
    add(218, 6500, "credit", "TURBOLOAN", "UPI/TURBOLOAN DISBURSAL/LOAN", "loan_disbursal")
    add(222, 7000, "credit", "INSTACASH", "UPI/INSTACASH DISBURSAL/LOAN", "loan_disbursal")

    # --- a genuine spending anomaly in the latest month: a medical bill ---
    add(236, 8400, "debit", "APOLLO PHARMACY", "POS/APOLLO PHARMACY/BILL", "healthcare")

    return txns


# ------------------------------------------------------------------ main


def seed() -> None:
    init_db()
    db = SessionLocal()

    try:
        # Fresh start for the demo user.
        existing = db.query(User).filter(User.phone_number == PHONE).first()
        if existing:
            db.delete(existing)       # cascade removes everything of theirs
            db.commit()

        user = User(
            name="Ravi Kumar",
            phone_number=PHONE,
            age=29,
            state="Karnataka",
            occupation="gig_worker",
            dependents=2,
            monthly_income_self_reported=30000,
            language="en",
            consent=True,
            consent_at=datetime.utcnow(),
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        txns = _build_transactions(user.id)
        db.add_all(txns)

        db.add_all([
            LoanOffer(user_id=user.id, lender_name="Bank A", principal=200000,
                      annual_rate_pct=11.0, tenure_months=36,
                      processing_fee=2000, insurance_fee=0),
            LoanOffer(user_id=user.id, lender_name="NBFC B", principal=200000,
                      annual_rate_pct=18.0, tenure_months=36,
                      processing_fee=5000, insurance_fee=3000),
            LoanOffer(user_id=user.id, lender_name="Fintech C", principal=200000,
                      annual_rate_pct=14.5, tenure_months=36,
                      processing_fee=3500, insurance_fee=1500),
        ])

        db.add(Goal(user_id=user.id, goal_name="Emergency fund",
                    target_amount=100000, months=18))

        if not db.query(SchemeRule).first():
            db.add_all([SchemeRule(**s) for s in SCHEMES])

        if not db.query(LenderPattern).first():
            db.add_all([LenderPattern(pattern=p, risk_note=n) for p, n in LENDER_PATTERNS])

        db.commit()

        # Index scheme PROSE in ChromaDB. Silently skipped if not installed.
        indexed = 0
        try:
            from app.rag import vector_store

            if vector_store.get_collection() is not None:
                by_code = {s["scheme_code"]: s for s in SCHEMES}
                for code, prose in SCHEME_PROSE.items():
                    meta = by_code[code]
                    indexed += vector_store.index_document(
                        scheme_code=code,
                        scheme_name=meta["scheme_name"],
                        text=prose,
                        state=meta["applicable_state"],
                        occupation=meta["applicable_occupation"],
                    )
        except Exception as exc:
            print(f"  (ChromaDB indexing skipped: {exc})")

        print("Seed complete.")
        print(f"  user_id       : {user.id}  ({user.name}, {user.age}, {user.state}, {user.occupation})")
        print(f"  phone         : {user.phone_number}")
        print(f"  transactions  : {len(txns)}")
        print(f"  loan offers   : 3")
        print(f"  schemes       : {db.query(SchemeRule).count()}")
        print(f"  prose chunks  : {indexed}")
        print()
        print("Try:")
        print(f'  curl -X POST localhost:8000/ask -H "Content-Type: application/json" \\')
        print(f'       -d \'{{"user_id": {user.id}, "query": "where is my money going?"}}\'')

    finally:
        db.close()


if __name__ == "__main__":
    seed()
