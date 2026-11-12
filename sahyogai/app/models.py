"""The 7 tables, written as Python classes instead of SQL.

Design decisions worth defending in an interview:

  1. `amount` is ALWAYS positive; direction lives in `txn_type`.
     If money in were +500 and money out -500, every query would have to
     remember the sign. Instead amount is a magnitude and txn_type says
     which way, so queries read obviously: WHERE txn_type = 'debit'.

  2. `raw_description` is kept next to `category`.
     category is a GUESS made by our code. If the guess is wrong you can
     look at the original bank line and fix it without re-uploading.
     Never throw away the evidence behind a derived value.

  3. The composite index (user_id, date, category).
     Nearly every query in this project is "this user, this date range,
     grouped by category". One index covering exactly that shape is the
     difference between instant and slow.

  4. nullable=True in scheme_rules means "NO CONSTRAINT on this dimension".
     A national scheme has applicable_state = NULL. A scheme open to all
     ages has max_age = NULL. That lets ONE query handle both national and
     state schemes instead of special-casing them.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False)
    phone_number = Column(String(20), unique=True, index=True)
    age = Column(Integer, nullable=True)
    state = Column(String(60), nullable=True)
    occupation = Column(String(60), nullable=True)
    dependents = Column(Integer, default=0)
    monthly_income_self_reported = Column(Float, nullable=True)
    language = Column(String(20), default="en")

    # Consent is recorded, not assumed. Required at signup by the API schema.
    consent = Column(Boolean, default=False, nullable=False)
    consent_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # cascade="all, delete-orphan" makes right-to-erasure ONE line:
    #     db.delete(user)   -> transactions, logs, goals, offers all go too
    transactions = relationship(
        "Transaction", back_populates="user", cascade="all, delete-orphan"
    )
    logs = relationship(
        "ConversationLog", back_populates="user", cascade="all, delete-orphan"
    )
    goals = relationship("Goal", back_populates="user", cascade="all, delete-orphan")
    loan_offers = relationship(
        "LoanOffer", back_populates="user", cascade="all, delete-orphan"
    )


class Transaction(Base):
    """The fact table. Everything else hangs off this."""

    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    date = Column(DateTime, index=True, nullable=False)
    amount = Column(Float, nullable=False)          # always positive
    txn_type = Column(String(10), nullable=False)   # "debit" or "credit"
    category = Column(String(40), index=True)
    merchant = Column(String(120))
    raw_description = Column(Text)                  # the original bank line
    source = Column(String(30), default="bank")     # bank | swiggy | zomato ...
    is_recurring = Column(Boolean, default=False)

    user = relationship("User", back_populates="transactions")

    __table_args__ = (
        Index("ix_user_date_category", "user_id", "date", "category"),
    )


class SchemeRule(Base):
    """Eligibility lives HERE, in SQL, not in the vector store.

    An embedding cannot enforce `age <= 40` — "aged 18 to 40" and
    "aged 60 and above" look almost identical as text. So every numeric or
    categorical constraint is a real column we can compare exactly.
    """

    __tablename__ = "scheme_rules"

    id = Column(Integer, primary_key=True)
    scheme_name = Column(String(160), nullable=False)
    scheme_code = Column(String(60), unique=True)
    benefit_summary = Column(Text)
    apply_url = Column(String(300))

    # NULL on any of these means "no constraint on this dimension".
    min_income = Column(Float, nullable=True)
    max_income = Column(Float, nullable=True)
    min_age = Column(Integer, nullable=True)
    max_age = Column(Integer, nullable=True)
    applicable_state = Column(String(60), nullable=True)
    applicable_occupation = Column(String(60), nullable=True)


class LoanOffer(Base):
    __tablename__ = "loan_offers"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    lender_name = Column(String(120))
    principal = Column(Float)
    annual_rate_pct = Column(Float)
    tenure_months = Column(Integer)
    processing_fee = Column(Float, default=0.0)
    insurance_fee = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="loan_offers")


class Goal(Base):
    __tablename__ = "goals"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    goal_name = Column(String(120))
    target_amount = Column(Float)
    months = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="goals")


class ConversationLog(Base):
    """Every question, which agent ran, and how long it took."""

    __tablename__ = "conversation_logs"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    channel = Column(String(20), default="web")
    query = Column(Text)
    tool_used = Column(String(60))
    routing_mode = Column(String(20))   # "llm" or "keyword"
    latency_ms = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="logs")


class LenderPattern(Base):
    """Curated list of lender names flagged in RBI / press advisories."""

    __tablename__ = "lender_patterns"

    id = Column(Integer, primary_key=True)
    pattern = Column(String(120), index=True)
    risk_note = Column(Text)
