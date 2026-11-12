"""parse -> categorise -> flag recurring -> deduplicate -> save.

Privacy note: the uploaded bytes live in memory for one function call only.
The raw file is NEVER written to disk. Only the derived rows persist.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from statistics import median

from sqlalchemy.orm import Session

from app.ingestion.categorizer import categorize_batch
from app.ingestion.parsers import parse_upload
from app.models import Transaction

log = logging.getLogger(__name__)


def detect_recurring(transactions: list[dict]) -> list[dict]:
    """Find subscriptions and EMIs — money leaking quietly.

    Three conditions define it exactly:
      1. same merchant at least 3 times
      2. amounts within 10% of the median (a subscription is a STABLE amount)
      3. median gap between charges is 25-35 days (roughly monthly)

    Why median, not average? One Rs.50,000 outlier drags an average badly.
    The median picks the middle value and ignores extremes.

    Why not ML? Three clear conditions define the thing exactly. A model
    would be harder to explain and no more accurate. Not everything needs ML.
    """
    by_merchant: dict[str, list[dict]] = defaultdict(list)
    for txn in transactions:
        if txn["txn_type"] == "debit":
            by_merchant[txn["merchant"]].append(txn)

    for merchant_txns in by_merchant.values():
        if len(merchant_txns) < 3:
            continue

        merchant_txns.sort(key=lambda t: t["date"])
        amounts = [t["amount"] for t in merchant_txns]
        median_amount = median(amounts)
        if median_amount <= 0:
            continue

        stable = all(abs(a - median_amount) / median_amount <= 0.10 for a in amounts)
        gaps = [
            (merchant_txns[i]["date"] - merchant_txns[i - 1]["date"]).days
            for i in range(1, len(merchant_txns))
        ]
        if not gaps:
            continue

        if stable and 25 <= median(gaps) <= 35:
            for txn in merchant_txns:
                txn["is_recurring"] = True

    return transactions


def _fingerprint(date, amount, merchant) -> tuple:
    """date + amount + merchant. Upload the same statement twice and
    nothing doubles."""
    return (date.date(), round(float(amount), 2), merchant)


def ingest_file(
    db: Session, user_id: int, filename: str, content: bytes
) -> dict:
    parsed = parse_upload(filename, content)        # 1. read
    if not parsed:
        return {"parsed": 0, "inserted": 0, "duplicates": 0, "recurring": 0}

    parsed = categorize_batch(parsed)               # 2. label
    parsed = detect_recurring(parsed)               # 3. flag subscriptions

    existing = {
        _fingerprint(t.date, t.amount, t.merchant)
        for t in db.query(Transaction).filter(Transaction.user_id == user_id).all()
    }

    inserted = duplicates = 0
    for txn in parsed:                              # 4. skip duplicates
        key = _fingerprint(txn["date"], txn["amount"], txn["merchant"])
        if key in existing:
            duplicates += 1
            continue
        existing.add(key)
        db.add(
            Transaction(
                user_id=user_id,
                date=txn["date"],
                amount=txn["amount"],
                txn_type=txn["txn_type"],
                category=txn.get("category", "other"),
                merchant=txn.get("merchant", "UNKNOWN"),
                raw_description=txn.get("raw_description", ""),
                source=txn.get("source", "bank"),
                is_recurring=txn.get("is_recurring", False),
            )
        )
        inserted += 1

    db.commit()
    # 5. `content` goes out of scope here. The raw file is never written to disk.

    return {
        "parsed": len(parsed),
        "inserted": inserted,
        "duplicates": duplicates,
        "recurring": sum(1 for t in parsed if t.get("is_recurring")),
    }
