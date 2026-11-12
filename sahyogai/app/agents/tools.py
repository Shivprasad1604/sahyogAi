"""The tool schemas handed to Claude.

The `description` field IS the routing logic. You are programming in
English here — vague descriptions produce wrong routing. Treat these
strings as code.
"""

TOOL_SCHEMAS = [
    {
        "name": "analyse_spending",
        "description": (
            "Break down where the user's money went: totals by category, unusual months, "
            "recurring subscriptions and EMIs, and food-delivery leakage. "
            "Use for 'where is my money going', 'what am I spending on', "
            "'which subscriptions am I paying for', 'am I spending too much on Swiggy'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "focus": {
                    "type": "string",
                    "enum": ["all", "subscriptions", "anomalies"],
                    "description": "Narrow the analysis. Use 'subscriptions' when the user asks about recurring charges.",
                },
                "months": {"type": "integer", "description": "How many months back to look. Default 3."},
            },
            "required": ["focus"],
        },
    },
    {
        "name": "forecast_spending",
        "description": (
            "Predict next month's spending and warn if the current month is running hot. "
            "Use for 'will I be short', 'how much will I spend next month', "
            "'am I overspending this month', 'can I afford my bills'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "focus": {
                    "type": "string",
                    "enum": ["forecast", "overspend"],
                    "description": "'overspend' when the user asks about the CURRENT month's pace.",
                }
            },
            "required": ["focus"],
        },
    },
    {
        "name": "savings_and_investment_guidance",
        "description": (
            "Measure how much the user can actually save, size an emergency fund, lay out a "
            "priority order for their money, and work out the monthly amount needed to reach a goal. "
            "Use for 'how much can I save', 'how do I build an emergency fund', "
            "'I want 2 lakh in 18 months', 'should I invest'. "
            "Never recommends a named fund, stock or policy."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "focus": {
                    "type": "string",
                    "enum": ["capacity", "emergency_fund", "goal"],
                    "description": "'goal' when a target amount and timeline are mentioned.",
                },
                "target_amount": {"type": "number", "description": "Goal amount in rupees, if stated."},
                "months": {"type": "integer", "description": "Months to reach the goal, if stated."},
            },
            "required": ["focus"],
        },
    },
    {
        "name": "loan_guidance",
        "description": (
            "EMI calculation, effective APR including fees, comparison across loan offers, and "
            "affordability (FOIR) checks against measured income. "
            "Use for 'can I afford an EMI of 6000', 'which loan should I take', "
            "'what is the EMI on 2 lakh at 12% for 3 years', 'is this interest rate good'. "
            "Do NOT use when the user is describing harassment or an abusive lender."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "focus": {
                    "type": "string",
                    "enum": ["affordability", "emi", "compare"],
                    "description": "'affordability' for 'can I afford', 'emi' when amount/rate/tenure are given, 'compare' for ranking offers.",
                },
                "proposed_emi": {"type": "number", "description": "The monthly EMI being considered."},
                "principal": {"type": "number", "description": "Loan amount in rupees."},
                "annual_rate_pct": {"type": "number", "description": "Annual interest rate as a percentage."},
                "tenure_months": {"type": "integer", "description": "Loan tenure in months."},
                "fees": {"type": "number", "description": "Processing and insurance fees deducted upfront."},
            },
            "required": ["focus"],
        },
    },
    {
        "name": "check_predatory_lending",
        "description": (
            "Scan transactions for predatory lending: known high-risk apps, very short "
            "high-cost loans, and loan stacking. Returns the user's rights under RBI rules "
            "and where to complain. "
            "Use for 'this app is harassing me', 'they are calling my family', "
            "'is this loan app legal', 'I think I'm in a debt trap', 'they have my contacts'. "
            "This takes priority over loan_guidance whenever harassment or legality is mentioned."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "find_welfare_schemes",
        "description": (
            "Find government schemes and subsidies the user is eligible for, using exact "
            "checks on age, income, state and occupation. "
            "Use for 'what schemes can I get', 'any yojana for me', 'am I eligible for a pension', "
            "'government subsidy for drivers', 'what benefits am I owed'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The user's question, passed through for document retrieval.",
                }
            },
        },
    },
]

ROUTER_SYSTEM = (
    "You route an Indian user's money question to exactly ONE specialist tool.\n"
    "You do NOT answer the question and you do NOT compute any number — a tool does that.\n"
    "Always call exactly one tool.\n"
    "Extract any numbers the user mentioned (EMI amounts, loan amounts, rates, tenures, "
    "goal amounts, timelines) into the tool's arguments.\n"
    "If the user mentions harassment, threats, contacts being called, or whether a lender is "
    "legal, choose check_predatory_lending even if a loan is also mentioned.\n"
    "If nothing else fits, choose analyse_spending."
)

SYNTH_SYSTEM = (
    "You explain a financial tool's result to an Indian user in plain, warm language.\n"
    "\n"
    "HARD RULES:\n"
    "- Use ONLY the numbers present in the tool result. Never estimate, adjust, "
    "round differently, extrapolate or invent a figure.\n"
    "- If a number the user asked for is not in the result, say it is not available.\n"
    "- Never recommend a specific fund, stock, insurance policy or lender by name.\n"
    "- Preserve any disclaimer present in the result.\n"
    "- Format rupees as Rs.1,234 style. Keep it under 180 words unless listing schemes.\n"
    "- Lead with the answer, then the one thing they should do next.\n"
    "- If the result contains rights or complaint channels, include them plainly."
)
