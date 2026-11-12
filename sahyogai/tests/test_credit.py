"""Financial maths verification.

The point of the whole "deterministic Python" principle is that an OUTSIDER
can check these numbers. Rs.8,884.88 is verifiable with any online EMI
calculator. That is impossible if a language model computes it.
"""

import pytest

from app.agents.credit import (
    affordability_check_with_income,
    compare_offers,
    effective_apr,
    emi,
    foir,
    total_interest,
    verdict,
)


# ------------------------------------------------------------------- EMI


def test_emi_known_value():
    """Rs.1,00,000 at 12% for 12 months -> Rs.8,884.88

    Check this against any online EMI calculator. That is the test."""
    assert emi(100000, 12, 12) == pytest.approx(8884.88, abs=0.5)


def test_emi_second_known_value():
    """Rs.2,00,000 at 11% for 36 months -> Rs.6,547.xx"""
    assert emi(200000, 11, 36) == pytest.approx(6547.0, abs=2.0)


def test_emi_zero_interest_is_simple_division():
    assert emi(120000, 0, 12) == pytest.approx(10000.0)


def test_emi_rises_with_rate():
    assert emi(100000, 18, 24) > emi(100000, 12, 24)


def test_emi_falls_with_longer_tenure():
    assert emi(100000, 12, 36) < emi(100000, 12, 12)


def test_emi_rejects_zero_months():
    with pytest.raises(ValueError):
        emi(100000, 12, 0)


def test_total_interest_is_positive():
    assert total_interest(100000, 12, 12) == pytest.approx(6618.6, abs=5.0)


# ------------------------------------------------------------------- APR


def test_effective_apr_exceeds_nominal_when_fees_exist():
    """The core claim of the whole feature: fees make the true cost higher
    than the quoted rate."""
    assert effective_apr(200000, 12.0, 36, fees=5000) > effective_apr(200000, 12.0, 36, fees=0)


def test_effective_apr_equals_nominal_without_fees():
    """With no fees, the effective APR should land back on the quoted rate."""
    assert effective_apr(200000, 12.0, 36, fees=0) == pytest.approx(12.0, abs=0.05)


def test_effective_apr_grows_with_fee_size():
    small = effective_apr(200000, 12.0, 36, fees=2000)
    large = effective_apr(200000, 12.0, 36, fees=8000)
    assert large > small


def test_effective_apr_rejects_fees_above_principal():
    with pytest.raises(ValueError):
        effective_apr(10000, 12.0, 12, fees=20000)


def test_bisection_is_deterministic():
    """Run it 20 times; a search that converges gives the identical answer
    every time. A language model would not."""
    results = {effective_apr(200000, 18.0, 36, fees=8000) for _ in range(20)}
    assert len(results) == 1


# -------------------------------------------------------------- comparison


def test_lower_quoted_rate_can_lose_to_fees():
    """The headline insight: the cheapest quoted rate is not always the
    cheapest loan."""
    result = compare_offers([
        {"lender_name": "Bank A", "principal": 200000, "annual_rate_pct": 11.0,
         "tenure_months": 36, "processing_fee": 2000},
        {"lender_name": "NBFC B", "principal": 200000, "annual_rate_pct": 18.0,
         "tenure_months": 36, "processing_fee": 5000, "insurance_fee": 3000},
    ])
    assert result["cheapest"] == "Bank A"
    assert result["offers"][0]["effective_apr_pct"] > 11.0     # fees pushed it up
    # Rs.24,578 of real money on the same Rs.2L over the same 36 months.
    assert result["saving_vs_costliest"] == pytest.approx(24578.49, abs=1.0)


def test_offers_are_ranked_by_true_apr_not_quoted_rate():
    result = compare_offers([
        {"lender_name": "High quote low fee", "principal": 100000,
         "annual_rate_pct": 14.0, "tenure_months": 24, "processing_fee": 0},
        {"lender_name": "Low quote huge fee", "principal": 100000,
         "annual_rate_pct": 12.0, "tenure_months": 24, "processing_fee": 12000},
    ])
    assert result["cheapest"] == "High quote low fee"


# ------------------------------------------------------------------ FOIR


def test_foir_known_value():
    """income Rs.40,000, existing EMIs Rs.6,000, new EMI Rs.6,000
       (6000 + 6000) / 40000 = 30%"""
    assert foir(income=40000, existing_emi=6000, proposed_emi=6000) == pytest.approx(30.0, abs=0.1)


def test_foir_verdict_boundaries():
    """The boundaries matter MORE than the arithmetic.

    The division is trivially correct. What can break is the BAND — 40.1%
    must say "Stretched" and not "Manageable", because that single word is
    what the user acts on. Test the thing that changes the decision.
    """
    assert verdict(29.9) == "Comfortable"
    assert verdict(30.1) == "Manageable"
    assert verdict(39.9) == "Manageable"
    assert verdict(40.1) == "Stretched"
    assert verdict(49.9) == "Stretched"
    assert verdict(50.1) == "Above typical approval"


def test_foir_zero_income_does_not_crash():
    """Measured income can genuinely be zero if no credits were parsed —
    a heavy-cash earner, say. "I don't have enough data to tell you" is a
    correct answer. A fabricated ratio is not.
    """
    result = affordability_check_with_income(income=0, proposed_emi=6000)
    assert result["verdict"] == "insufficient_income_data"
    assert "foir_pct" not in result


def test_foir_rejects_negative_income():
    with pytest.raises(ValueError):
        foir(income=-100, existing_emi=0, proposed_emi=5000)


def test_affordability_reports_headroom():
    result = affordability_check_with_income(income=40000, proposed_emi=5000, existing_emi=5000)
    assert result["foir_pct"] == pytest.approx(25.0, abs=0.1)
    assert result["verdict"] == "Comfortable"
    assert result["headroom_to_50pct"] == pytest.approx(10000.0, abs=1.0)


def test_disclaimer_is_always_present():
    result = affordability_check_with_income(income=40000, proposed_emi=6000)
    assert "disclaimer" in result
