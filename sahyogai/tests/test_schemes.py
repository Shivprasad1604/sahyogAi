"""Eligibility correctness — the project's headline architecture claim.

These tests exist to prove the thing that justified the whole MySQL +
ChromaDB split: a 29-year-old must NEVER be shown a 60-and-above pension
scheme, no matter how similar the two scheme descriptions look as text.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agents.schemes import eligible_schemes
from app.database import Base
from app.models import SchemeRule, User
from app.rag.chunking import chunk_text


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    session.add_all([
        # Nearly identical TEXT to the one below. Opposite AGE rule.
        SchemeRule(scheme_name="Youth pension scheme", scheme_code="YOUTH",
                   benefit_summary="This scheme is available to citizens aged 18 to 40.",
                   min_age=18, max_age=40),
        SchemeRule(scheme_name="Old age pension scheme", scheme_code="OLD",
                   benefit_summary="This scheme is available to citizens aged 60 and above.",
                   min_age=60, max_age=None),
        SchemeRule(scheme_name="Karnataka gig worker fund", scheme_code="KA",
                   benefit_summary="For gig workers in Karnataka.",
                   min_age=18, max_age=60,
                   applicable_state="Karnataka", applicable_occupation="gig_worker"),
        SchemeRule(scheme_name="Maharashtra worker fund", scheme_code="MH",
                   benefit_summary="For unorganised workers in Maharashtra.",
                   min_age=18, max_age=60, applicable_state="Maharashtra"),
        SchemeRule(scheme_name="Open to everyone", scheme_code="ALL",
                   benefit_summary="No age, income, state or occupation limits.",
                   min_age=None, max_age=None),
    ])
    session.commit()
    yield session
    session.close()


def _user(**kwargs):
    defaults = dict(name="Test", phone_number="+910000000000", consent=True)
    return User(**{**defaults, **kwargs})


# ------------------------------------------------------- THE core test


def test_29_year_old_never_sees_a_60_plus_scheme(db):
    """THE test. This is the bug that justified the architecture.

    An embedding sees "aged 18 to 40" and "aged 60 and above" as nearly
    identical — same words, same structure, same topic. Similarity search
    can confidently return the wrong one. A SQL predicate cannot.
    """
    codes = {s.scheme_code for s in eligible_schemes(db, _user(age=29))}
    assert "YOUTH" in codes
    assert "OLD" not in codes


def test_65_year_old_sees_only_the_older_scheme(db):
    """The mirror image. Both directions must hold."""
    codes = {s.scheme_code for s in eligible_schemes(db, _user(age=65))}
    assert "OLD" in codes
    assert "YOUTH" not in codes


@pytest.mark.parametrize("age,should_include,should_exclude", [
    (17, [], ["YOUTH", "OLD"]),
    (18, ["YOUTH"], ["OLD"]),
    (40, ["YOUTH"], ["OLD"]),      # boundary: inclusive
    (41, [], ["YOUTH", "OLD"]),    # boundary: one year over
    (59, [], ["OLD"]),
    (60, ["OLD"], ["YOUTH"]),      # boundary: inclusive
])
def test_age_boundaries_are_exact(db, age, should_include, should_exclude):
    """Boundaries are where a fuzzy system gets it wrong and an exact one
    does not. Test every edge."""
    codes = {s.scheme_code for s in eligible_schemes(db, _user(age=age))}
    for code in should_include:
        assert code in codes, f"age {age} should qualify for {code}"
    for code in should_exclude:
        assert code not in codes, f"age {age} should NOT qualify for {code}"


# ---------------------------------------------------------------- state


def test_wrong_state_scheme_never_surfaces(db):
    """A Karnataka user must not see a Maharashtra-only scheme, however
    well the text matches."""
    codes = {s.scheme_code for s in eligible_schemes(db, _user(age=29, state="Karnataka"))}
    assert "MH" not in codes


def test_national_scheme_surfaces_for_every_state(db):
    """NULL means 'no constraint on this dimension', so one query handles
    both national and state schemes."""
    for state in ("Karnataka", "Maharashtra", "Bihar"):
        codes = {s.scheme_code for s in eligible_schemes(db, _user(age=29, state=state))}
        assert "ALL" in codes


def test_occupation_filter_applies(db):
    gig = {s.scheme_code for s in eligible_schemes(
        db, _user(age=29, state="Karnataka", occupation="gig_worker"))}
    farmer = {s.scheme_code for s in eligible_schemes(
        db, _user(age=29, state="Karnataka", occupation="farmer"))}
    assert "KA" in gig
    assert "KA" not in farmer


def test_user_with_no_profile_still_gets_unconstrained_schemes(db):
    codes = {s.scheme_code for s in eligible_schemes(db, _user())}
    assert "ALL" in codes


# ------------------------------------------------------------- chunking


def test_chunking_keeps_eligibility_clauses_intact():
    """Fixed-size splitting would cut a clause in half —
    'available to citizens aged 18 to' [chunk ends]. That is exactly the
    content that must stay whole."""
    text = (
        "This scheme is available to citizens aged 18 to 40 years.\n\n"
        "How to apply: visit a Common Service Centre with your Aadhaar card.\n\n"
        "Documents required: Aadhaar, bank passbook, mobile number."
    )
    chunks = chunk_text(text, chunk_size=900)
    assert any("aged 18 to 40" in c for c in chunks)


def test_chunking_packs_whole_paragraphs():
    text = "\n\n".join(f"Paragraph number {i} with some text in it." for i in range(5))
    chunks = chunk_text(text, chunk_size=900)
    assert len(chunks) == 1          # all five fit in one chunk


def test_chunking_splits_only_oversized_paragraphs():
    chunks = chunk_text("word " * 500, chunk_size=200)
    assert len(chunks) > 1
    assert all(len(c) <= 200 for c in chunks)
