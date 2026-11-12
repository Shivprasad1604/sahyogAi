"""Agent 2 — forecast. "Will I be short this month?"

Two methods, chosen by how much history exists:
  - Prophet          when there are 6+ months (finds trend and seasonality)
  - moving average   otherwise

Why have both: Prophet on 3 data points is worse than a moving average and
less honest about it. The system picks based on available data and ALWAYS
reports which method ran, plus its error.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import Transaction, User

log = logging.getLogger(__name__)


def _monthly_series(db: Session, user_id: int) -> list[tuple[str, float]]:
    rows = (
        db.query(Transaction.date, Transaction.amount)
        .filter(Transaction.user_id == user_id, Transaction.txn_type == "debit")
        .all()
    )
    buckets: dict[str, float] = defaultdict(float)
    for date, amount in rows:
        buckets[f"{date.year:04d}-{date.month:02d}"] += float(amount)
    return sorted(buckets.items())


def _baseline_forecast(series: list[tuple[str, float]]) -> dict:
    """3-month moving average, with MAE measured on the history we have."""
    values = [v for _, v in series]
    window = values[-3:] if len(values) >= 3 else values
    prediction = sum(window) / len(window)

    # Walk-forward error: predict each month from the 3 before it.
    errors = []
    for i in range(3, len(values)):
        guess = sum(values[i - 3 : i]) / 3
        errors.append(abs(values[i] - guess))
    mae = sum(errors) / len(errors) if errors else None

    return {
        "method": "moving_average_3m",
        "predicted_spend": round(prediction, 2),
        "mae": round(mae, 2) if mae is not None else None,
        "note": "Not enough history for a seasonal model, so a 3-month moving average was used.",
    }


def _prophet_forecast(series: list[tuple[str, float]]) -> dict | None:
    try:
        import pandas as pd
        from prophet import Prophet
    except Exception as exc:
        log.info("Prophet unavailable (%s); using the moving-average baseline.", exc)
        return None

    try:
        df = pd.DataFrame(
            {
                "ds": [datetime.strptime(m, "%Y-%m") for m, _ in series],
                "y": [v for _, v in series],
            }
        )
        model = Prophet(yearly_seasonality=False, weekly_seasonality=False, daily_seasonality=False)
        model.fit(df)
        future = model.make_future_dataframe(periods=1, freq="MS")
        prediction = model.predict(future)

        fitted = prediction.iloc[: len(df)]["yhat"].tolist()
        mae = sum(abs(a - b) for a, b in zip(df["y"].tolist(), fitted)) / len(fitted)
        row = prediction.iloc[-1]

        return {
            "method": "prophet",
            "predicted_spend": round(float(row["yhat"]), 2),
            "lower_bound": round(float(row["yhat_lower"]), 2),
            "upper_bound": round(float(row["yhat_upper"]), 2),
            "mae": round(float(mae), 2),
            "note": "Trend-aware forecast fitted on your monthly history.",
        }
    except Exception as exc:
        log.warning("Prophet failed (%s); falling back to the baseline.", exc)
        return None


def forecast_next_month(db: Session, user_id: int) -> dict:
    series = _monthly_series(db, user_id)
    if len(series) < 2:
        return {
            "method": "insufficient_data",
            "predicted_spend": None,
            "message": "At least two months of transactions are needed before a forecast means anything.",
        }

    result = None
    if len(series) >= 6:
        result = _prophet_forecast(series)
    if result is None:
        result = _baseline_forecast(series)

    result["months_of_history"] = len(series)
    result["history"] = [{"month": m, "spend": round(v, 2)} for m, v in series]
    return result


def overspend_alert(db: Session, user_id: int) -> dict:
    """Project the current month's PACE and warn on the 12th, while the user
    can still act — not on the 30th, when it is too late."""
    today = datetime.utcnow()
    rows = (
        db.query(Transaction.date, Transaction.amount)
        .filter(Transaction.user_id == user_id, Transaction.txn_type == "debit")
        .all()
    )
    spent_so_far = sum(
        float(a) for d, a in rows if d.year == today.year and d.month == today.month
    )

    predicted = forecast_next_month(db, user_id).get("predicted_spend")
    day = max(1, today.day)
    projected = spent_so_far / day * 30

    alert = predicted is not None and projected > predicted * 1.15
    return {
        "day_of_month": today.day,
        "spent_so_far": round(spent_so_far, 2),
        "projected_month_end": round(projected, 2),
        "normal_month": predicted,
        "alert": alert,
        "message": (
            f"At this pace you'll end the month around Rs.{projected:,.0f}, "
            f"about 15%+ above your usual Rs.{predicted:,.0f}."
            if alert
            else "Your spending pace is in line with a normal month."
        ),
    }


def run(db: Session, user: User, focus: str = "forecast", **_) -> dict:
    if focus == "overspend":
        return {"kind": "overspend", **overspend_alert(db, user.id)}
    result = {"kind": "forecast", **forecast_next_month(db, user.id)}
    result["overspend"] = overspend_alert(db, user.id)
    return result
