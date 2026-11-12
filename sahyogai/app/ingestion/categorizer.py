"""Two-tier categorisation.

A favourite interview question: "why not just send everything to the LLM?"

              Rules first        LLM for everything
  Speed       instant            slow
  Cost        free               pays per row
  Reproducible same every run    can vary between runs
  Coverage    ~80%               100%

Rules handle the bulk instantly and identically every run; the LLM handles
only genuine unknowns, batched into ONE call. That is a cost / latency /
determinism engineering decision, not laziness.
"""

from __future__ import annotations

import logging

from app.llm import call_json

log = logging.getLogger(__name__)

CATEGORY_RULES: dict[str, list[str]] = {
    "food_delivery":   ["SWIGGY", "ZOMATO", "DOMINOS", "KFC", "MCDONALD", "EATCLUB", "BOX8"],
    "quick_commerce":  ["ZEPTO", "BLINKIT", "INSTAMART", "BIGBASKET", "DUNZO", "GROFERS"],
    "transport":       ["UBER", "OLA", "RAPIDO", "IRCTC", "PETROL", "FASTAG", "HPCL", "IOCL", "BPCL", "METRO"],
    "groceries":       ["DMART", "RELIANCE FRESH", "MORE SUPER", "KIRANA", "SUPERMARKET"],
    "rent":            ["RENT", "LANDLORD", "NOBROKER", "HOUSING"],
    "utilities":       ["ELECTRICITY", "BESCOM", "MSEB", "GAS", "WATER BILL", "BROADBAND", "AIRTEL", "JIO", "VI ", "BSNL"],
    "subscriptions":   ["NETFLIX", "SPOTIFY", "PRIME", "HOTSTAR", "YOUTUBE", "SONYLIV", "ZEE5"],
    "loan_repayment":  ["EMI", "LOAN", "NBFC", "BAJAJ FIN", "HDFC LOAN", "KREDITBEE", "MONEYVIEW", "NAVI"],
    "insurance":       ["INSURANCE", "LIC", "POLICYBAZAAR", "HDFC LIFE", "STAR HEALTH"],
    "healthcare":      ["PHARMACY", "APOLLO", "MEDPLUS", "HOSPITAL", "CLINIC", "1MG", "PHARMEASY"],
    "education":       ["SCHOOL", "COLLEGE", "TUITION", "FEES", "BYJU", "UNACADEMY"],
    "investments":     ["ZERODHA", "GROWW", "UPSTOX", "MUTUAL FUND", "SIP", "NPS", "PPF"],
    "cash_withdrawal": ["ATM", "CASH WDL", "CASH WITHDRAWAL"],
    "shopping":        ["AMAZON", "FLIPKART", "MYNTRA", "AJIO", "MEESHO", "NYKAA"],
    "income":          ["SALARY", "PAYOUT", "SETTLEMENT", "EARNINGS", "CREDIT INTEREST"],
}

VALID_CATEGORIES = set(CATEGORY_RULES) | {"other"}


def rule_categorize(merchant: str, description: str, txn_type: str) -> str:
    """Tier 1. Returns 'other' when nothing matches — the signal to try tier 2."""
    haystack = f"{merchant} {description}".upper()

    # Money coming in is income unless it is clearly a loan disbursal.
    if txn_type == "credit":
        if any(k in haystack for k in ("LOAN", "NBFC", "DISBURS", "KREDITBEE", "NAVI")):
            return "loan_disbursal"
        return "income"

    for category, keywords in CATEGORY_RULES.items():
        for keyword in keywords:
            if keyword in haystack:
                return category
    return "other"


_LLM_SYSTEM = (
    "You label Indian bank-statement merchant names with a spending category.\n"
    "Reply with ONLY a JSON object mapping each merchant to one category from "
    "this list: " + ", ".join(sorted(VALID_CATEGORIES)) + ".\n"
    "Use 'other' when genuinely unsure. Do not invent categories."
)


def llm_categorize(merchants: list[str]) -> dict[str, str]:
    """Tier 2. ONE API call for ALL unknown merchants, not one call per row."""
    if not merchants:
        return {}
    result = call_json(_LLM_SYSTEM, "Merchants:\n" + "\n".join(merchants))
    if not result:
        return {}
    return {
        merchant: category
        for merchant, category in result.items()
        if isinstance(category, str) and category in VALID_CATEGORIES
    }


def categorize_batch(transactions: list[dict]) -> list[dict]:
    # Tier 1 for everything.
    for txn in transactions:
        if txn.get("category"):      # order history already knows its category
            continue
        txn["category"] = rule_categorize(
            txn.get("merchant", ""), txn.get("raw_description", ""), txn["txn_type"]
        )

    # Tier 2 only for the leftovers, deduplicated, in one call.
    unknown = sorted({t["merchant"] for t in transactions if t["category"] == "other"})
    if not unknown:
        return transactions

    mapping = llm_categorize(unknown)
    if mapping:
        for txn in transactions:
            if txn["category"] == "other":
                txn["category"] = mapping.get(txn["merchant"], "other")
        log.info("LLM categorised %d/%d unknown merchants", len(mapping), len(unknown))

    return transactions
