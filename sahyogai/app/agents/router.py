"""The router — the centrepiece.

One question comes in. Six specialists exist. Which one runs?

    "can I afford an EMI of 6000?"
            |
    Router decides: loan_guidance, focus=affordability, emi=6000
            |
    credit.affordability_check(db, user, 6000)
            |
    {"foir_pct": 47.7, "verdict": "Stretched", ...}
            |
    LLM phrases it in plain language

The LLM appears at the TOP (choosing) and the BOTTOM (wording), and never
in the middle. Remove it and the middle still works.
"""

from __future__ import annotations

import json
import logging
import time

from sqlalchemy.orm import Session

from app.agents import credit, forecast, loan_shield, savings, schemes, spending
from app.agents.tools import ROUTER_SYSTEM, SYNTH_SYSTEM, TOOL_SCHEMAS
from app.llm import call_text, call_with_tools
from app.models import ConversationLog, User

log = logging.getLogger(__name__)


# ------------------------------------------------------------ Path A: LLM


def llm_route(query: str) -> tuple[str, dict] | None:
    """The LLM returns a TOOL NAME and ARGUMENTS — not an answer."""
    response = call_with_tools(
        messages=[{"role": "user", "content": query}],
        tools=TOOL_SCHEMAS,
        system=ROUTER_SYSTEM,
    )
    if response is None:
        return None

    for block in response.content:
        if block.type == "tool_use":
            return block.name, dict(block.input)
    return None


# -------------------------------------------------------- Path B: keywords

# ORDER MATTERS. "Is this loan app harassing me?" contains both "loan" and
# "harass". The shield is listed first, so it wins. Put the more specific
# tool higher.
KEYWORD_ROUTES: list[tuple[str, list[str]]] = [
    ("check_predatory_lending", [
        "harass", "harassing", "threaten", "predatory", "loan app", "debt trap",
        "calling my family", "calling my contacts", "abusive", "recovery agent",
        "is this legal", "illegal", "blackmail", "my contacts",
    ]),
    ("find_welfare_schemes", [
        "scheme", "yojana", "subsidy", "pension", "government benefit",
        "welfare", "eligible for", "am i owed", "benefits",
    ]),
    ("loan_guidance", [
        "emi", "loan", "borrow", "interest rate", "afford", "apr",
        "repayment", "tenure", "lender", "principal",
    ]),
    ("savings_and_investment_guidance", [
        "save", "saving", "invest", "sip", "emergency fund", "goal",
        "how much can i put aside", "corpus", "surplus",
    ]),
    ("forecast_spending", [
        "forecast", "predict", "next month", "overspend", "will i be short",
        "run out", "end of month", "on track",
    ]),
    ("analyse_spending", [
        "spend", "spent", "spending", "subscription", "swiggy", "zomato",
        "breakdown", "where is my money", "where did my money", "category",
    ]),
]

_AMOUNT_WORDS = {"lakh": 100000, "lakhs": 100000, "crore": 10000000, "k": 1000}


def _extract_numbers(query: str) -> list[float]:
    """Pull rupee amounts out of plain text, handling 'lakh' and 'k'."""
    import re

    found: list[float] = []
    for match in re.finditer(
        r"(\d[\d,]*\.?\d*)\s*(lakhs?|crores?|k)?", query.lower()
    ):
        raw, unit = match.group(1), match.group(2)
        try:
            value = float(raw.replace(",", ""))
        except ValueError:
            continue
        if unit:
            value *= _AMOUNT_WORDS.get(unit.rstrip("s"), 1)
        found.append(value)
    return found


def keyword_route(query: str) -> tuple[str, dict]:
    """A PURE FUNCTION — same input always gives the same output. That is
    exactly why it can be tested against a labelled set and given an
    accuracy number."""
    lowered = query.lower()

    for tool_name, keywords in KEYWORD_ROUTES:
        if any(keyword in lowered for keyword in keywords):
            return tool_name, _args_for(tool_name, lowered)

    return "analyse_spending", {"focus": "all"}


def _args_for(tool_name: str, lowered: str) -> dict:
    numbers = _extract_numbers(lowered)

    if tool_name == "loan_guidance":
        if any(w in lowered for w in ("afford", "can i take", "should i take", "foir")):
            emi_value = next((n for n in numbers if 500 <= n <= 200000), None)
            return {"focus": "affordability", "proposed_emi": emi_value}
        if any(w in lowered for w in ("compare", "which loan", "better offer", "cheaper")):
            return {"focus": "compare"}
        big = [n for n in numbers if n >= 10000]
        if big:
            rate = next((n for n in numbers if 1 <= n <= 60), None)
            months = next((int(n) for n in numbers if 3 <= n <= 360 and n != rate), None)
            return {
                "focus": "emi",
                "principal": big[0],
                "annual_rate_pct": rate,
                "tenure_months": months,
            }
        return {"focus": "affordability", "proposed_emi": numbers[0] if numbers else None}

    if tool_name == "savings_and_investment_guidance":
        if "emergency" in lowered:
            return {"focus": "emergency_fund"}
        big = [n for n in numbers if n >= 10000]
        if big and any(w in lowered for w in ("month", "year", "by ", "in ", "want")):
            months = next((int(n) for n in numbers if 1 <= n <= 360), None)
            return {"focus": "goal", "target_amount": big[0], "months": months}
        return {"focus": "capacity"}

    if tool_name == "analyse_spending":
        if "subscription" in lowered or "recurring" in lowered:
            return {"focus": "subscriptions"}
        if "unusual" in lowered or "anomaly" in lowered or "spike" in lowered:
            return {"focus": "anomalies"}
        return {"focus": "all"}

    if tool_name == "forecast_spending":
        if any(w in lowered for w in ("this month", "overspend", "on track", "pace")):
            return {"focus": "overspend"}
        return {"focus": "forecast"}

    return {}


# ---------------------------------------------------------------- dispatch


def _run_spending(db, user, args):   return spending.run(db, user, **args)
def _run_forecast(db, user, args):   return forecast.run(db, user, **args)
def _run_savings(db, user, args):    return savings.run(db, user, **args)
def _run_credit(db, user, args):     return credit.run(db, user, **args)
def _run_shield(db, user, args):     return loan_shield.run(db, user, **args)
def _run_schemes(db, user, args):    return schemes.run(db, user, **args)


# Name -> function. Adding a seventh agent is ONE new entry here.
DISPATCH = {
    "analyse_spending": _run_spending,
    "forecast_spending": _run_forecast,
    "savings_and_investment_guidance": _run_savings,
    "loan_guidance": _run_credit,
    "check_predatory_lending": _run_shield,
    "find_welfare_schemes": _run_schemes,
}


# --------------------------------------------------------------- synthesis


def _format_deterministically(tool_name: str, result: dict) -> str:
    """The no-LLM path. Prints the SAME numbers, just less smoothly."""
    if result.get("message") and result.get("error"):
        return result["message"]

    lines: list[str] = []
    kind = result.get("kind", tool_name)

    if kind == "spending":
        lines.append(
            f"Over {result['months_covered']} months you spent Rs.{result['total_spend']:,.0f} "
            f"(about Rs.{result['monthly_avg_spend']:,.0f} a month)."
        )
        for category in result.get("categories", [])[:5]:
            lines.append(
                f"  - {category['category']}: Rs.{category['total']:,.0f} ({category['share_pct']}%)"
            )
        for anomaly in result.get("anomalies", [])[:3]:
            lines.append(
                f"  ! {anomaly['category']} was Rs.{anomaly['latest_month']:,.0f} last month "
                f"versus a usual Rs.{anomaly['usual_month']:,.0f}."
            )
        leakage = result.get("delivery_leakage")
        if leakage:
            lines.append(
                f"  Food delivery: {leakage['orders']} orders at Rs.{leakage['avg_order_value']:,.0f} "
                f"average. Halving that frees about Rs.{leakage['if_halved_annual_saving']:,.0f} a year."
            )

    elif kind == "subscriptions":
        lines.append(
            f"You have {len(result['subscriptions'])} recurring charges costing "
            f"Rs.{result['total_monthly']:,.0f} a month (Rs.{result['total_annual']:,.0f} a year)."
        )
        for sub in result["subscriptions"][:8]:
            lines.append(f"  - {sub['merchant']}: Rs.{sub['monthly_amount']:,.0f}/month")

    elif kind == "forecast":
        if result.get("predicted_spend") is None:
            lines.append(result.get("message", "Not enough history to forecast yet."))
        else:
            lines.append(
                f"Next month's projected spending is Rs.{result['predicted_spend']:,.0f} "
                f"(method: {result['method']}"
                + (f", average error Rs.{result['mae']:,.0f}" if result.get("mae") else "")
                + ")."
            )
            overspend = result.get("overspend", {})
            if overspend.get("message"):
                lines.append(f"  {overspend['message']}")

    elif kind == "overspend":
        lines.append(result["message"])

    elif kind == "affordability":
        if result.get("verdict") == "insufficient_income_data":
            lines.append(result["message"])
        else:
            lines.append(
                f"On a measured income of Rs.{result['monthly_income']:,.0f}, an EMI of "
                f"Rs.{result['proposed_emi']:,.0f} puts your FOIR at {result['foir_pct']}% "
                f"- {result['verdict']}. {result['detail']}"
            )

    elif kind == "emi":
        lines.append(
            f"EMI is Rs.{result['emi']:,.2f} a month. Over {result['tenure_months']} months you "
            f"repay Rs.{result['total_repayment']:,.0f}, of which Rs.{result['total_interest']:,.0f} "
            f"is interest. True cost including fees: {result['effective_apr_pct']}% APR."
        )

    elif kind == "compare":
        if not result.get("offers"):
            lines.append(result.get("message", "No offers saved."))
        else:
            lines.append("Ranked by true cost (effective APR, fees included):")
            for offer in result["offers"]:
                lines.append(
                    f"  - {offer['lender']}: quoted {offer['quoted_rate_pct']}%, "
                    f"EMI Rs.{offer['emi']:,.0f}, true APR {offer['effective_apr_pct']}%, "
                    f"total cost Rs.{offer['total_cost_over_principal']:,.0f}"
                )
            if result.get("saving_vs_costliest"):
                lines.append(
                    f"  Choosing {result['cheapest']} saves Rs.{result['saving_vs_costliest']:,.0f}."
                )

    elif kind == "savings":
        lines.append(
            f"Income Rs.{result['monthly_income']:,.0f}/month ({result['income_source']}), "
            f"spending Rs.{result['monthly_spend']:,.0f}, surplus Rs.{result['monthly_surplus']:,.0f}."
        )
        for step in result.get("allocation_framework", []):
            lines.append(f"  {step['priority']}. {step['step']} - {step['detail']}")

    elif kind == "emergency_fund":
        lines.append(
            f"Target Rs.{result['target_amount']:,.0f} ({result['months_of_cover']} months of "
            f"essential spending). {result['reason']}"
        )

    elif kind == "goal":
        lines.append(f"To reach Rs.{result['target_amount']:,.0f} in {result['months']} months:")
        for row in result["monthly_required"]:
            lines.append(
                f"  - at {row['assumed_annual_return_pct']}% a year: "
                f"Rs.{row['monthly_investment']:,.0f}/month"
            )

    elif kind == "loan_shield":
        lines.append(result["summary"])
        for finding in result.get("findings", [])[:5]:
            lines.append(f"  ! {finding['why']}")
        lines.append("Your rights:")
        for right in result["your_rights"][:4]:
            lines.append(f"  - {right['right']} ({right['rule']})")
        lines.append("Where to complain:")
        for channel in result["complaint_channels"]:
            suffix = f" - {channel['url']}" if channel["url"] else ""
            lines.append(f"  - {channel['name']}{suffix}")

    elif kind == "schemes":
        lines.append(f"You are eligible for {result['eligible_count']} scheme(s).")
        for scheme in result.get("schemes", []):
            lines.append(f"  - {scheme['scheme_name']}: {scheme['benefit_summary']}")
            if scheme.get("apply_url"):
                lines.append(f"    Apply: {scheme['apply_url']}")

    else:
        lines.append(json.dumps(result, indent=2, default=str))

    if result.get("disclaimer"):
        lines.append(f"\n{result['disclaimer']}")

    return "\n".join(lines)


def synthesise(query: str, tool_name: str, result: dict) -> tuple[str, str]:
    """Ask the LLM to EXPLAIN, not compute. Returns (answer, mode)."""
    payload = (
        f"User asked: {query}\n\n"
        f"Tool that ran: {tool_name}\n\n"
        f"Tool result (these numbers are final and correct):\n"
        f"{json.dumps(result, indent=2, default=str)}"
    )
    answer = call_text(SYNTH_SYSTEM, payload)
    if answer:
        return answer, "llm"
    return _format_deterministically(tool_name, result), "deterministic"


# ------------------------------------------------------------ entry point


def handle_query(db: Session, user_id: int, query: str, channel: str = "web") -> dict:
    started = time.perf_counter()

    user = db.get(User, user_id)
    if user is None:
        raise ValueError(f"User {user_id} not found")

    # Path A, then Path B. Both merge into the same line afterwards.
    routed = llm_route(query)
    if routed is not None:
        tool_name, args = routed
        routing_mode = "llm"
    else:
        tool_name, args = keyword_route(query)
        routing_mode = "keyword"

    if tool_name not in DISPATCH:
        tool_name, args, routing_mode = "analyse_spending", {"focus": "all"}, "fallback"

    if tool_name == "find_welfare_schemes":
        args.setdefault("question", query)

    args = {k: v for k, v in args.items() if v is not None}

    result = DISPATCH[tool_name](db, user, args)
    answer, synthesis_mode = synthesise(query, tool_name, result)

    latency_ms = int((time.perf_counter() - started) * 1000)

    db.add(
        ConversationLog(
            user_id=user_id,
            channel=channel,
            query=query,
            tool_used=tool_name,
            routing_mode=routing_mode,
            latency_ms=latency_ms,
        )
    )
    db.commit()

    return {
        "answer": answer,
        "tool_used": tool_name,
        "tool_args": args,
        "routing_mode": routing_mode,
        "synthesis_mode": synthesis_mode,
        "data": result,
        "latency_ms": latency_ms,
    }
