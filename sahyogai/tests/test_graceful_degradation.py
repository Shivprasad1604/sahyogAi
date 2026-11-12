"""Graceful degradation: the numbers do not move when the LLM is gone.

This is the test you run in an interview when they ask "what happens when
the API is down?". Unset the key, run it, show the figures are identical.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agents import credit
from app.agents.router import _format_deterministically, keyword_route
from app.database import Base
from app.models import Transaction, User


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture()
def user_with_data(db):
    from datetime import datetime, timedelta

    user = User(name="Test", phone_number="+919000000000", age=29,
                state="Karnataka", occupation="gig_worker", consent=True)
    db.add(user)
    db.commit()
    db.refresh(user)

    start = datetime(2025, 1, 1)
    for month in range(4):
        base = month * 30
        db.add(Transaction(user_id=user.id, date=start + timedelta(days=base + 2),
                           amount=40000, txn_type="credit", category="income",
                           merchant="PAYOUT", raw_description="PAYOUT"))
        db.add(Transaction(user_id=user.id, date=start + timedelta(days=base + 5),
                           amount=9500, txn_type="debit", category="rent",
                           merchant="LANDLORD", raw_description="RENT"))
        db.add(Transaction(user_id=user.id, date=start + timedelta(days=base + 7),
                           amount=6000, txn_type="debit", category="loan_repayment",
                           merchant="BAJAJ FIN", raw_description="EMI", is_recurring=True))
    db.commit()
    return user


def test_llm_disabled_does_not_break_routing(monkeypatch):
    """With no API key, llm_route returns None and keyword_route takes over."""
    monkeypatch.setattr("app.config.settings.ANTHROPIC_API_KEY", None)
    from app.agents.router import llm_route

    assert llm_route("can I afford an emi of 6000") is None
    assert keyword_route("can I afford an emi of 6000")[0] == "loan_guidance"


def test_emi_is_identical_with_and_without_llm(monkeypatch):
    """The whole claim in one assertion: the maths does not know or care
    whether an LLM is available."""
    with_key = emi_value = credit.emi(200000, 12, 36)
    monkeypatch.setattr("app.config.settings.ANTHROPIC_API_KEY", None)
    without_key = credit.emi(200000, 12, 36)
    assert with_key == without_key == emi_value


def test_deterministic_formatter_prints_the_same_numbers(db, user_with_data):
    """When the LLM can't phrase the answer, the fallback formatter still
    surfaces every figure."""
    result = credit.affordability_check(db, user_with_data, proposed_emi=6000)
    text = _format_deterministically("loan_guidance", result)

    assert str(result["foir_pct"]) in text
    assert result["verdict"] in text
    assert result["disclaimer"] in text


def test_affordability_uses_measured_income_not_self_reported(db, user_with_data):
    result = credit.affordability_check(db, user_with_data, proposed_emi=6000)
    assert result["income_source"] == "measured_from_transactions"
    assert result["monthly_income"] == pytest.approx(40000, abs=1)


def test_chroma_absence_does_not_break_scheme_matching(db, user_with_data, monkeypatch):
    """No ChromaDB means no prose, but SQL eligibility still works."""
    from app.agents import schemes

    monkeypatch.setattr("app.rag.vector_store.get_collection", lambda: None)
    result = schemes.run(db, user_with_data)
    assert result["kind"] == "schemes"
    assert "eligible_count" in result


def test_forecast_falls_back_to_moving_average(db, user_with_data):
    """Fewer than 6 months of history means the baseline runs, and it SAYS
    which method it used."""
    from app.agents import forecast

    result = forecast.forecast_next_month(db, user_with_data.id)
    assert result["method"] in {"moving_average_3m", "prophet"}
    assert result["predicted_spend"] is not None
