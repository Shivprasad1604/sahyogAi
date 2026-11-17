# SahyogAI

**A financial assistant for people whose income is irregular — delivery riders, cab drivers, small shop owners.**

Ask a question in plain language on web, WhatsApp or voice. It answers from your own bank and UPI data.

Architecturally it is a **router agent dispatching to six specialists**. The LLM picks the specialist and phrases the answer — but **every financial number is computed in deterministic Python**, so it is verifiable.

```
100 tests passing · 6 specialist agents · 3 channels · runs with zero API keys
```

---

## The problem

Someone drives for a delivery app. One month ₹28,000, the next ₹36,000. Money leaves through UPI, a food app, an EMI, a subscription they forgot about. Ask them five questions:

| Question | Reality |
|---|---|
| Where did your money go last month? | Scattered across a statement and three apps |
| Will you be short this month? | They find out on the 28th, too late to act |
| This loan is 1.5% per month — is that cheap? | It is 18%+ a year, more once fees are counted |
| This app is calling your family — is that legal? | It is not. RBI rules forbid it. They don't know |
| A scheme is paying you money — are you signed up? | They qualify. The rules sit in a bureaucratic PDF |

Every answer **already exists** somewhere. The information is not missing — it is fragmented, generic, English-heavy, and delivered on channels people don't open.

That makes it a distribution problem, which makes it an engineering problem.

---

## The design rule everything follows

> **The AI never calculates a number. It only decides which calculator to run, and how to phrase the result.**

LLMs are non-deterministic and unreliable at arithmetic — the same prompt can give different numbers. In lending, a plausible-but-wrong figure is a real harm. So the calculator was taken away from the AI.

Every EMI, APR and FOIR figure is ordinary Python, unit-tested against values anyone can verify with an online calculator.

---

## The six specialists

| Agent | Answers | Does |
|---|---|---|
| `spending.py` | Where did my money go? | GROUP BY category, z-score anomalies, delivery leakage |
| `forecast.py` | Will I be short? | Prophet with 6+ months, else moving average — always reports which |
| `savings.py` | How much can I save? | Measured income − spend, emergency fund, SIP solver |
| `credit.py` | Is this loan cheap? | EMI formula, effective APR by bisection, FOIR |
| `loan_shield.py` | Is this legal? | Known lenders, short-tenure cost shape, loan stacking + RBI rights |
| `schemes.py` | What am I owed? | SQL eligibility + ChromaDB prose |

Each agent is a plain Python module with **no AI inside**. It takes the database and returns a dictionary of numbers — never a sentence. The sentence is written later, by the LLM.

### How a question flows

```
         "can I afford an EMI of 6000?"
                      |
            main.py  POST /ask           validate, check user exists
                      |
                 router.py                which agent should run?
                /                \
      LLM tool-calling      keyword rules      (fallback when API is down)
                \                /
                 v              v
         loan_guidance, {focus: affordability, emi: 6000}
                      |
             DISPATCH dictionary
                      |
    ┌─────┬─────┬─────┼─────┬─────┬─────┐
  spending forecast savings CREDIT shield schemes     ← only ONE runs
                      |
              credit.py: SQL → income & existing EMIs
                        maths → FOIR = 34.2%
                      |
        {"foir_pct": 34.2, "verdict": "Manageable"}
                      |
              synthesise()  — LLM words it, numbers locked
                      |
   "On a measured income of ₹31,650, an EMI of ₹6,000 puts your
    FOIR at 34.2% — manageable, but little room for a bad month."
```

Three things to notice: only **one** agent runs per question; the LLM appears at the **top and bottom but never the middle**; and both routing paths merge into the same line afterwards.

---

## Why MySQL *and* ChromaDB

This split is the architectural centrepiece, and it came out of a real bug.

A 29-year-old test user asked "which pension scheme can I join?" and was shown a scheme with an age floor of **60**, described confidently. Logging the retrieved chunks showed why: the correct and incorrect schemes scored within a few hundredths of each other.

```
A: "This scheme is available to citizens aged 18 to 40."
B: "This scheme is available to citizens aged 60 and above."
```

Same words, same structure, same topic. Their embeddings are nearly identical. **An embedding has no way to represent `age ≤ 40`.** No amount of better chunking, a higher `n_results`, or a reranker fixes that — the information needed to rank correctly isn't in the similarity score at all.

So the document is split **by the type of data**, not stored as one thing:

| Data | Store | Why |
|---|---|---|
| Age limits, income caps, state, occupation | **MySQL** | Exact comparison: `WHERE min_age <= 29` |
| What the benefit is, how to apply | **ChromaDB** | Needs meaning-based search |

The SQL decides **who qualifies**. The vector store supplies the **explanation**. Neither can do the other's job. The metadata filter also runs *before* similarity ranking, so a wrong-state scheme cannot surface at all.

Proven in `tests/test_schemes.py` — including both directions and every age boundary.

---

## Quick start

```bash
git clone https://github.com/<your-username>/sahyogai.git
cd sahyogai

python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python -m scripts.seed_data                           # creates a demo user with 275 transactions
uvicorn app.main:app --reload
```

Open http://localhost:8000/docs for the interactive API.

**No API key, no MySQL, no ChromaDB needed.** It runs on SQLite with keyword routing, and every financial number is identical.

```bash
curl -X POST localhost:8000/ask -H "Content-Type: application/json" \
     -d '{"user_id": 1, "query": "can I afford an EMI of 6000?"}'
```

Demo UI: `streamlit run ui/streamlit_app.py`

---

## Graceful degradation

| Missing | What breaks | What still works |
|---|---|---|
| Claude API key | Natural phrasing, smart routing | Keyword routing, **all numbers** |
| ChromaDB | Scheme prose retrieval | SQL eligibility matching |
| Prophet | Trend-aware forecasting | Moving-average forecast |
| MySQL | — | SQLite fallback |
| Twilio | WhatsApp | Web + voice |

**Nothing in the "still works" column is a financial number that changes.** Check `GET /health` to see exactly which pieces are live.

To demo it: unset `ANTHROPIC_API_KEY`, re-run the same query, compare the figures.

---

## Tests

```bash
pytest tests/ -q        # 100 passed
```

| File | Covers |
|---|---|
| `test_routing.py` | 24-query labelled eval set, 24/24; order-matters test; determinism |
| `test_credit.py` | EMI vs known values, APR vs nominal, FOIR bands, zero-income guard |
| `test_schemes.py` | **The 29-vs-60 test**, every age boundary, state and occupation filters |
| `test_ingestion.py` | Fuzzy columns, merchant cleaning, recurring detection, dedup |
| `test_graceful_degradation.py` | Numbers identical with and without the LLM |

Two worth reading:

```python
def test_emi_known_value():
    # Verify this against any online EMI calculator. That is the test.
    assert emi(100000, 12, 12) == pytest.approx(8884.88, abs=0.5)

def test_29_year_old_never_sees_a_60_plus_scheme(db):
    codes = {s.scheme_code for s in eligible_schemes(db, _user(age=29))}
    assert "YOUTH" in codes and "OLD" not in codes
```

---

## Real output from the seeded demo user

**Loan comparison** — same ₹2L, same 36 months:

| Lender | Quoted | EMI | Total cost | **True APR** |
|---|---|---|---|---|
| Bank A | 11.0% | ₹6,548 | ₹35,719 | **11.70%** |
| Fintech C | 14.5% | ₹6,884 | ₹47,831 | **16.30%** |
| NBFC B | 18.0% | ₹7,230 | ₹60,297 | **20.97%** |

Choosing Bank A saves **₹24,578**. The lowest quoted rate is not always the cheapest loan.

**Predatory lending detected without knowing the lender's name:**

```
₹5,000 came in and ₹6,000 went back out 20 days later.
That is about 365% a year.
```

That is caught purely from the *shape* of the money movement — a credit followed by a larger debit to the same merchant within 60 days.

---

## Project layout

```
app/
  config.py          settings + secrets, with fallbacks for everything
  database.py        engine, session, get_db
  models.py          the 7 tables
  schemas.py         Pydantic request/response validation
  llm.py             Claude wrapper — returns None instead of raising
  ingestion/
    parsers.py       CSV fuzzy matching, PDF regex, order history
    categorizer.py   two-tier: rules first, LLM for leftovers only
    pipeline.py      parse → categorise → recurring → dedup → save
  agents/
    spending.py forecast.py savings.py credit.py loan_shield.py schemes.py
    tools.py         tool schemas — the descriptions ARE the routing logic
    router.py        LLM routing + keyword fallback + dispatch + synthesis
  rag/
    chunking.py      paragraph-aware, so eligibility clauses stay intact
    vector_store.py  ChromaDB — prose only, never eligibility
  channels/
    whatsapp.py      Twilio webhook, STOP/DELETE opt-out
    voice.py         Whisper → same router → gTTS
  main.py            FastAPI endpoints
scripts/seed_data.py
tests/
ui/streamlit_app.py
```

### Adding a seventh agent

```
1. Create app/agents/insurance.py        ← new file
2. Add a tool description in tools.py    ← so the LLM knows when to pick it
3. Add one line to DISPATCH              ← "insurance_check": _run_insurance
```

Zero changes to the six existing agents. Zero changes to the router logic. That property is the argument for the pattern.

---

## Known limits

Stated openly, because knowing your limits is part of the work:

- **No authentication.** The API trusts a user ID. Production needs JWT and phone verification. This is the weakest part.
- **Scanned PDFs are not supported.** The parser reads text *inside* a PDF; a scanned statement is an image. That needs OCR (Tesseract).
- **Cash spending is invisible.** For heavy-cash users the picture is incomplete by design.
- **No caching.** Scheme lookups and forecasts recompute on every query. Redis would fix it.
- **Streamlit** is right for an MVP, not for concurrency.
- Merchant extraction is **regex and string cleaning, not NER**. Indian bank narrations follow predictable patterns once UPI/POS prefixes and reference numbers are stripped, so regex was more reliable and faster for this format.

---

## Safety and privacy

- **Consent is structurally required** at signup — a Pydantic field, not a checkbox we hope someone ticked.
- **Raw uploads are never written to disk.** The bytes live in memory for one function call; only derived rows persist.
- **Right to erasure is one line** — `cascade="all, delete-orphan"` means `db.delete(user)` removes transactions, logs, goals and offers too. Reply `DELETE` on WhatsApp does the same.
- **No named recommendations.** The synthesis prompt forbids naming any fund, stock, policy or lender.
- **Every warning carries its RBI reference** and the four real complaint channels — RBI CMS, Sachet, cybercrime.gov.in, and the police.

---

## Stack

FastAPI · SQLAlchemy · Pydantic · MySQL/SQLite · ChromaDB · Claude API (tool-calling) · Prophet · PyMuPDF · pandas · Twilio · Whisper · gTTS · Streamlit · pytest
