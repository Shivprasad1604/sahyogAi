"""Agent 6 — schemes. "What money am I owed?"

THE ARCHITECTURE CENTREPIECE. Three steps:

  1. SQL decides eligibility. Exactly.
     An embedding has no concept of `age <= 40`. "Aged 18 to 40" and "aged
     60 and above" are nearly identical as text, so similarity search can
     confidently tell a 29-year-old they qualify for a pension scheme they
     do not. A numeric constraint needs a numeric evaluator.

  2. The vector store supplies the EXPLANATION, filtered first.
     The `where` clause runs before similarity ranking, and we additionally
     restrict to the scheme codes SQL already approved — so a scheme the
     user is ineligible for cannot surface at all.

  3. The LLM narrates the joined result, told explicitly never to invent
     criteria. Without an LLM, a deterministic formatter prints the same
     facts.

The SQL decides WHO qualifies. The vector store supplies the EXPLANATION.
Neither can do the other's job.
"""

from __future__ import annotations

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import SchemeRule, User
from app.rag import vector_store


def eligible_schemes(db: Session, user: User) -> list[SchemeRule]:
    """Step 1 — exact eligibility, as SQL predicates.

    Read each filter as: "no constraint set on this dimension, OR I satisfy it."
    That is what nullable=True buys us: one query handles national and state
    schemes without special-casing.
    """
    query = db.query(SchemeRule)

    if user.age is not None:
        query = query.filter(
            or_(SchemeRule.min_age.is_(None), SchemeRule.min_age <= user.age),
            or_(SchemeRule.max_age.is_(None), SchemeRule.max_age >= user.age),
        )

    if user.state:
        query = query.filter(
            or_(
                SchemeRule.applicable_state.is_(None),
                SchemeRule.applicable_state == user.state,
            )
        )

    if user.occupation:
        query = query.filter(
            or_(
                SchemeRule.applicable_occupation.is_(None),
                SchemeRule.applicable_occupation == user.occupation,
            )
        )

    return query.all()


def _measured_annual_income(db: Session, user: User) -> float | None:
    from app.agents.savings import measure_income

    info = measure_income(db, user)
    if info["income_source"] == "unknown":
        return None
    return info["monthly_income"] * 12


def match(db: Session, user: User, question: str = "") -> dict:
    schemes = eligible_schemes(db, user)

    # Income filter applied in Python so we can SAY which income we used.
    annual_income = _measured_annual_income(db, user)
    if annual_income is not None:
        schemes = [
            s
            for s in schemes
            if (s.min_income is None or s.min_income <= annual_income)
            and (s.max_income is None or s.max_income >= annual_income)
        ]

    codes = [s.scheme_code for s in schemes if s.scheme_code]

    # Step 2 — prose, restricted to the schemes SQL already approved.
    passages = []
    if codes:
        passages = vector_store.search(
            question or "what is this benefit and how do I apply",
            state=user.state,
            occupation=user.occupation,
            scheme_codes=codes,
            n_results=5,
        )

    prose_by_code: dict[str, list[str]] = {}
    for passage in passages:
        prose_by_code.setdefault(passage["scheme_code"], []).append(passage["text"])

    return {
        "kind": "schemes",
        "user_profile_used": {
            "age": user.age,
            "state": user.state,
            "occupation": user.occupation,
            "annual_income_used": round(annual_income, 2) if annual_income else None,
            "income_source": "measured_from_transactions" if annual_income else "unknown",
        },
        "eligible_count": len(schemes),
        "schemes": [
            {
                "scheme_name": s.scheme_name,
                "scheme_code": s.scheme_code,
                "benefit_summary": s.benefit_summary,
                "apply_url": s.apply_url,
                "eligibility_matched_on": {
                    "min_age": s.min_age,
                    "max_age": s.max_age,
                    "min_income": s.min_income,
                    "max_income": s.max_income,
                    "state": s.applicable_state or "any",
                    "occupation": s.applicable_occupation or "any",
                },
                "details_from_documents": prose_by_code.get(s.scheme_code, []),
            }
            for s in schemes
        ],
        "retrieval_available": bool(passages) or vector_store.get_collection() is not None,
        "note": (
            "Eligibility was decided by exact SQL comparison on your age, state, "
            "occupation and measured income. Document text is shown only for schemes "
            "you already qualify for."
        ),
        "disclaimer": (
            "Scheme rules change. Confirm on the official portal before applying. "
            "No eligibility criteria here were generated by a language model."
        ),
    }


def run(db: Session, user: User, question: str = "", **_) -> dict:
    return match(db, user, question)
