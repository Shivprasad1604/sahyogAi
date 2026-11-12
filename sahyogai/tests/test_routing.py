"""Routing accuracy as a measurable number.

keyword_route is a PURE FUNCTION — same input, same output, every time.
That is exactly why it can be tested against a labelled set and given an
accuracy figure instead of a feeling.

Note the queries are written the way REAL USERS type them ("they are
harassing me"), not in technical language. That is what makes the number
mean anything.
"""

import pytest

from app.agents.router import keyword_route

EVAL_SET = [
    # --- spending ---
    ("where is my money going", "analyse_spending"),
    ("where did my money go last month", "analyse_spending"),
    ("show me my spending breakdown", "analyse_spending"),
    ("how much am I spending on swiggy", "analyse_spending"),
    ("what subscriptions am I paying for", "analyse_spending"),

    # --- forecast ---
    ("will I be short this month", "forecast_spending"),
    ("how much will I spend next month", "forecast_spending"),
    ("am I going to overspend", "forecast_spending"),
    ("predict my expenses", "forecast_spending"),

    # --- savings ---
    ("how much can I save every month", "savings_and_investment_guidance"),
    ("I want to build an emergency fund", "savings_and_investment_guidance"),
    ("should I start a sip", "savings_and_investment_guidance"),
    ("how do I invest my extra money", "savings_and_investment_guidance"),

    # --- credit ---
    ("can I afford an emi of 5000", "loan_guidance"),
    ("what is the emi on 2 lakh", "loan_guidance"),
    ("which loan should I take", "loan_guidance"),
    ("is 18 percent interest rate good", "loan_guidance"),

    # --- loan shield (listed FIRST, so it wins over "loan") ---
    ("they are harassing me for repayment", "check_predatory_lending"),
    ("this loan app is calling my family", "check_predatory_lending"),
    ("is this loan app legal", "check_predatory_lending"),
    ("I think I am in a debt trap", "check_predatory_lending"),

    # --- schemes ---
    ("what schemes can I get", "find_welfare_schemes"),
    ("is there any yojana for me", "find_welfare_schemes"),
    ("am I eligible for a pension", "find_welfare_schemes"),
]


@pytest.mark.parametrize("query,expected", EVAL_SET)
def test_routing(query, expected):
    """@parametrize runs this once per row — 24 separate tests from one
    function."""
    assert keyword_route(query)[0] == expected


def test_routing_accuracy_is_reported():
    correct = sum(1 for q, e in EVAL_SET if keyword_route(q)[0] == e)
    accuracy = correct / len(EVAL_SET) * 100
    print(f"\nKeyword routing accuracy: {correct}/{len(EVAL_SET)} ({accuracy:.0f}%)")
    assert accuracy == 100.0


def test_shield_beats_loan_when_both_words_present():
    """ORDER MATTERS. 'Is this loan app harassing me?' contains both 'loan'
    and 'harass'. The shield is listed first, so it wins.

    Move loan_guidance above check_predatory_lending in KEYWORD_ROUTES and
    THIS is the test that fails.
    """
    tool, _ = keyword_route("this loan app is harassing me about my emi")
    assert tool == "check_predatory_lending"


def test_unknown_query_falls_back_to_spending():
    assert keyword_route("hello")[0] == "analyse_spending"


def test_route_is_deterministic():
    """Same input, same output. Run it 50 times to prove the point."""
    outputs = {keyword_route("can I afford an emi of 6000")[0] for _ in range(50)}
    assert len(outputs) == 1


def test_numbers_are_extracted_into_args():
    tool, args = keyword_route("can I afford an emi of 6000")
    assert tool == "loan_guidance"
    assert args["focus"] == "affordability"
    assert args["proposed_emi"] == 6000
