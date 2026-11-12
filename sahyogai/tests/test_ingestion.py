"""Ingestion: parsing, categorising, recurring detection, deduplication."""

from datetime import datetime, timedelta

import pytest

from app.ingestion.categorizer import rule_categorize
from app.ingestion.parsers import clean_merchant, parse_bank_csv, parse_date
from app.ingestion.pipeline import detect_recurring


# ------------------------------------------------------------- merchant


@pytest.mark.parametrize("raw,expected_contains", [
    ("UPI/SWIGGY BANGALORE/428193847/PAY", "SWIGGY"),
    ("POS/ZEPTO MARKETPLACE/9823749283", "ZEPTO"),
    ("NEFT/LANDLORD RENT/TXN8273", "LANDLORD"),
    ("UPI-NETFLIX INDIA-SUBSCRIPTION", "NETFLIX"),
])
def test_clean_merchant_strips_noise(raw, expected_contains):
    assert expected_contains in clean_merchant(raw)


def test_clean_merchant_removes_long_reference_numbers():
    assert "428193847" not in clean_merchant("UPI/SWIGGY/428193847/PAY")


def test_clean_merchant_handles_empty_input():
    assert clean_merchant("") == "UNKNOWN"


# ----------------------------------------------------------------- dates


@pytest.mark.parametrize("value", ["12/03/2025", "12-03-2025", "2025-03-12", "12-Mar-2025"])
def test_parse_date_handles_indian_formats(value):
    parsed = parse_date(value)
    assert parsed is not None
    assert parsed.year == 2025 and parsed.month == 3 and parsed.day == 12


def test_parse_date_returns_none_on_garbage():
    assert parse_date("not a date") is None


# ------------------------------------------------------------------- CSV


def test_csv_fuzzy_column_matching():
    """Every bank names its columns differently. Substring matching is what
    stops the parser breaking on the next bank."""
    csv = (
        "Txn Date,Narration,Withdrawal Amt,Deposit Amt\n"
        "03/04/2025,UPI/SWIGGY BANGALORE/9283/PAY,412.00,\n"
        "05/04/2025,SALARY CREDIT,,28000.00\n"
    )
    rows = parse_bank_csv(csv.encode())
    assert len(rows) == 2
    assert rows[0]["txn_type"] == "debit" and rows[0]["amount"] == 412.0
    assert rows[1]["txn_type"] == "credit" and rows[1]["amount"] == 28000.0


def test_csv_with_completely_different_column_names():
    csv = (
        "Value Date,Particulars,Debit,Credit\n"
        "03/04/2025,POS/DMART/8273,3200.00,\n"
    )
    rows = parse_bank_csv(csv.encode())
    assert len(rows) == 1
    assert "DMART" in rows[0]["merchant"]


def test_csv_amount_is_always_positive():
    """Direction lives in txn_type, never in the sign of the amount."""
    csv = "Date,Description,Amount\n03/04/2025,SOME PAYMENT,-500.00\n"
    rows = parse_bank_csv(csv.encode())
    assert rows[0]["amount"] == 500.0
    assert rows[0]["txn_type"] == "debit"


def test_csv_without_required_columns_raises():
    with pytest.raises(ValueError):
        parse_bank_csv(b"Foo,Bar\n1,2\n")


# -------------------------------------------------------- categorisation


@pytest.mark.parametrize("merchant,expected", [
    ("SWIGGY BANGALORE", "food_delivery"),
    ("ZEPTO MARKETPLACE", "quick_commerce"),
    ("UBER INDIA", "transport"),
    ("BAJAJ FIN EMI", "loan_repayment"),
    ("NETFLIX INDIA", "subscriptions"),
    ("LANDLORD RENT", "rent"),
])
def test_rules_cover_common_indian_merchants(merchant, expected):
    assert rule_categorize(merchant, "", "debit") == expected


def test_unknown_merchant_returns_other():
    """'other' is the SIGNAL to try tier 2, not a failure."""
    assert rule_categorize("ZZQQ TRADERS", "", "debit") == "other"


def test_credits_are_income_unless_loan_like():
    assert rule_categorize("SWIGGY PAYOUT", "", "credit") == "income"
    assert rule_categorize("QUICKRUPEE DISBURSAL", "LOAN", "credit") == "loan_disbursal"


def test_rule_categorize_is_deterministic():
    results = {rule_categorize("SWIGGY", "", "debit") for _ in range(50)}
    assert results == {"food_delivery"}


# ----------------------------------------------------------- recurring


def _txn(day_offset, amount, merchant="NETFLIX"):
    return {
        "date": datetime(2025, 1, 1) + timedelta(days=day_offset),
        "amount": amount,
        "txn_type": "debit",
        "merchant": merchant,
        "raw_description": merchant,
    }


def test_stable_monthly_charge_is_flagged_recurring():
    txns = [_txn(0, 199), _txn(30, 199), _txn(61, 199), _txn(91, 199)]
    assert all(t["is_recurring"] for t in detect_recurring(txns))


def test_small_variation_still_counts_as_recurring():
    """Within 10% of the median is close enough for a subscription."""
    txns = [_txn(0, 199), _txn(30, 205), _txn(61, 195)]
    assert all(t["is_recurring"] for t in detect_recurring(txns))


def test_wildly_varying_amounts_are_not_recurring():
    txns = [_txn(0, 199), _txn(30, 850), _txn(61, 120)]
    assert not any(t.get("is_recurring") for t in detect_recurring(txns))


def test_wrong_gap_is_not_recurring():
    """Weekly charges are not a monthly subscription."""
    txns = [_txn(0, 199), _txn(7, 199), _txn(14, 199), _txn(21, 199)]
    assert not any(t.get("is_recurring") for t in detect_recurring(txns))


def test_fewer_than_three_charges_is_not_recurring():
    txns = [_txn(0, 199), _txn(30, 199)]
    assert not any(t.get("is_recurring") for t in detect_recurring(txns))


def test_median_ignores_a_single_outlier():
    """Why median and not average: one Rs.50,000 outlier would drag an
    average badly. The median just picks the middle value."""
    txns = [_txn(0, 199), _txn(30, 199), _txn(61, 199), _txn(91, 199), _txn(121, 199)]
    assert all(t["is_recurring"] for t in detect_recurring(txns))
