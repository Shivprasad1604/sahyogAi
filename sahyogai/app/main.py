"""FastAPI application.

Four things to notice in any endpoint below:
  @app.post(...)          a decorator that turns a function into an endpoint
  payload: QueryRequest   Pydantic validates the JSON BEFORE our code runs
  Depends(get_db)         dependency injection; FastAPI opens and closes the session
  HTTPException(404, ..)  a proper status code, not a crash
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from app.agents import credit
from app.agents.router import handle_query
from app.config import settings
from app.database import get_db, init_db
from app.ingestion.pipeline import ingest_file
from app.models import Goal, LoanOffer, User
from app.schemas import (
    EmiRequest,
    GoalCreate,
    LoanOfferCreate,
    QueryRequest,
    QueryResponse,
    UploadResult,
    UserCreate,
    UserOut,
)

logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="SahyogAI",
    description=(
        "A financial assistant for irregular earners. A router agent dispatches to six "
        "specialists; the LLM chooses the specialist and phrases the answer, but every "
        "financial number is computed in deterministic Python."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    init_db()
    logging.info(
        "SahyogAI up. LLM=%s  DB=%s",
        "enabled" if settings.llm_enabled else "disabled (keyword routing)",
        "sqlite" if settings.using_sqlite else "mysql",
    )


# ------------------------------------------------------------------ health


@app.get("/health")
def health() -> dict:
    """Shows exactly which optional pieces are present. Nothing in the
    'still works' column is a financial number that changes."""
    def _available(module: str) -> bool:
        import importlib.util

        return importlib.util.find_spec(module) is not None

    return {
        "status": "ok",
        "llm_enabled": settings.llm_enabled,
        "routing": "llm_with_keyword_fallback" if settings.llm_enabled else "keyword_only",
        "database": "sqlite" if settings.using_sqlite else "mysql",
        "chromadb_installed": _available("chromadb"),
        "prophet_installed": _available("prophet"),
        "note": "All financial calculations are identical regardless of the above.",
    }


# ------------------------------------------------------------------- users


@app.post("/users", response_model=UserOut, status_code=201)
def create_user(payload: UserCreate, db: Session = Depends(get_db)) -> User:
    if not payload.consent:
        raise HTTPException(
            400,
            "Explicit consent is required before any financial data can be processed.",
        )

    existing = db.query(User).filter(User.phone_number == payload.phone_number).first()
    if existing:
        raise HTTPException(409, "This phone number is already registered.")

    user = User(**payload.model_dump(), consent_at=datetime.utcnow())
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@app.get("/users/{user_id}", response_model=UserOut)
def get_user(user_id: int, db: Session = Depends(get_db)) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    return user


@app.delete("/users/{user_id}", status_code=204)
def delete_user(user_id: int, db: Session = Depends(get_db)) -> None:
    """Right to erasure. cascade="all, delete-orphan" on the model means
    transactions, logs, goals and offers all go with the user."""
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    db.delete(user)
    db.commit()


# ------------------------------------------------------------------- ask


@app.post("/ask", response_model=QueryResponse)
def ask(payload: QueryRequest, db: Session = Depends(get_db)) -> dict:
    if db.get(User, payload.user_id) is None:
        raise HTTPException(404, "User not found")
    return handle_query(db, payload.user_id, payload.query, payload.channel)


# ------------------------------------------------------------------ upload


@app.post("/upload-statement/{user_id}", response_model=UploadResult)
async def upload_statement(
    user_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)
) -> dict:
    if db.get(User, user_id) is None:
        raise HTTPException(404, "User not found")

    content = await file.read()
    try:
        return ingest_file(db, user_id, file.filename or "upload.csv", content)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    # `content` goes out of scope here; the raw file is never written to disk.


# ---------------------------------------------------------------- insights


@app.get("/insights/spending/{user_id}")
def insights_spending(user_id: int, months: int = 3, db: Session = Depends(get_db)) -> dict:
    from app.agents import spending

    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    return spending.run(db, user, focus="all", months=months)


@app.get("/insights/forecast/{user_id}")
def insights_forecast(user_id: int, db: Session = Depends(get_db)) -> dict:
    from app.agents import forecast

    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    return forecast.run(db, user)


@app.get("/insights/savings/{user_id}")
def insights_savings(user_id: int, db: Session = Depends(get_db)) -> dict:
    from app.agents import savings

    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    return savings.run(db, user)


@app.get("/insights/schemes/{user_id}")
def insights_schemes(user_id: int, db: Session = Depends(get_db)) -> dict:
    from app.agents import schemes

    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    return schemes.run(db, user)


@app.get("/insights/loan-shield/{user_id}")
def insights_loan_shield(user_id: int, db: Session = Depends(get_db)) -> dict:
    from app.agents import loan_shield

    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    return loan_shield.run(db, user)


# ------------------------------------------------------------------- loans


@app.post("/loan-offers/{user_id}", status_code=201)
def add_loan_offer(
    user_id: int, payload: LoanOfferCreate, db: Session = Depends(get_db)
) -> dict:
    if db.get(User, user_id) is None:
        raise HTTPException(404, "User not found")
    offer = LoanOffer(user_id=user_id, **payload.model_dump())
    db.add(offer)
    db.commit()
    db.refresh(offer)
    return {"id": offer.id, "lender_name": offer.lender_name}


@app.get("/loan-offers/{user_id}/compare")
def compare_loan_offers(user_id: int, db: Session = Depends(get_db)) -> dict:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    return credit.run(db, user, focus="compare")


@app.post("/calculate/emi")
def calculate_emi(payload: EmiRequest) -> dict:
    """A pure calculator — no user, no database, no AI. Verify it against
    any online EMI calculator."""
    monthly = credit.emi(payload.principal, payload.annual_rate_pct, payload.tenure_months)
    return {
        "emi": round(monthly, 2),
        "total_repayment": round(monthly * payload.tenure_months, 2),
        "total_interest": round(monthly * payload.tenure_months - payload.principal, 2),
        "effective_apr_pct": credit.effective_apr(
            payload.principal, payload.annual_rate_pct, payload.tenure_months, payload.fees
        ),
        "disclaimer": credit.DISCLAIMER,
    }


# ------------------------------------------------------------------- goals


@app.post("/goals/{user_id}", status_code=201)
def add_goal(user_id: int, payload: GoalCreate, db: Session = Depends(get_db)) -> dict:
    if db.get(User, user_id) is None:
        raise HTTPException(404, "User not found")
    goal = Goal(user_id=user_id, **payload.model_dump())
    db.add(goal)
    db.commit()
    db.refresh(goal)
    return {"id": goal.id, "goal_name": goal.goal_name}


# ---------------------------------------------------------------- channels

from app.channels.voice import router as voice_router  # noqa: E402
from app.channels.whatsapp import router as whatsapp_router  # noqa: E402

app.include_router(whatsapp_router)
app.include_router(voice_router)
