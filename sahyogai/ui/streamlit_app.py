"""A small Streamlit demo UI.

    streamlit run ui/streamlit_app.py

Right for an MVP, not built for concurrency — replacing it is the first
thing listed under "what I'd do at scale" in the README.
"""

import json

import requests
import streamlit as st

API = st.sidebar.text_input("API base URL", "http://localhost:8000")

st.set_page_config(page_title="SahyogAI", page_icon="₹", layout="wide")
st.title("SahyogAI")
st.caption(
    "Ask about your money in plain language. Every rupee figure is computed in "
    "deterministic Python — the AI only picks the specialist and phrases the answer."
)

try:
    health = requests.get(f"{API}/health", timeout=5).json()
    cols = st.sidebar.columns(2)
    cols[0].metric("LLM", "on" if health["llm_enabled"] else "off")
    cols[1].metric("DB", health["database"])
    st.sidebar.caption(f"Routing: {health['routing']}")
    st.sidebar.caption(health["note"])
except Exception:
    st.sidebar.error("API not reachable. Start it with: uvicorn app.main:app --reload")

user_id = st.sidebar.number_input("User ID", min_value=1, value=1, step=1)

st.sidebar.divider()
st.sidebar.subheader("Upload a statement")
uploaded = st.sidebar.file_uploader("Bank CSV/PDF or Swiggy export", type=["csv", "pdf", "xlsx"])
if uploaded and st.sidebar.button("Ingest"):
    response = requests.post(
        f"{API}/upload-statement/{user_id}",
        files={"file": (uploaded.name, uploaded.getvalue())},
        timeout=120,
    )
    if response.ok:
        st.sidebar.success(response.json())
    else:
        st.sidebar.error(response.text)

EXAMPLES = [
    "where is my money going?",
    "can I afford an EMI of 6000?",
    "this loan app is calling my family, is that legal?",
    "what government schemes am I eligible for?",
    "will I be short this month?",
    "how much can I save every month?",
]

st.subheader("Ask")
chosen = st.selectbox("Try an example", [""] + EXAMPLES)
query = st.text_input("Your question", value=chosen)

if st.button("Ask", type="primary") and query.strip():
    with st.spinner("Routing to a specialist..."):
        response = requests.post(
            f"{API}/ask",
            json={"user_id": int(user_id), "query": query, "channel": "web"},
            timeout=120,
        )

    if not response.ok:
        st.error(response.text)
    else:
        result = response.json()

        meta = st.columns(4)
        meta[0].metric("Specialist", result["tool_used"])
        meta[1].metric("Routing", result["routing_mode"])
        meta[2].metric("Narration", result["synthesis_mode"])
        meta[3].metric("Latency", f"{result['latency_ms']} ms")

        st.markdown("### Answer")
        st.write(result["answer"])

        with st.expander("Raw tool result — every number above came from here"):
            st.code(json.dumps(result["data"], indent=2), language="json")

st.divider()
st.subheader("Insights")
tabs = st.tabs(["Spending", "Forecast", "Savings", "Loan shield", "Schemes"])
endpoints = ["spending", "forecast", "savings", "loan-shield", "schemes"]

for tab, endpoint in zip(tabs, endpoints):
    with tab:
        if st.button(f"Load {endpoint}", key=endpoint):
            response = requests.get(f"{API}/insights/{endpoint}/{user_id}", timeout=60)
            if response.ok:
                st.json(response.json())
            else:
                st.error(response.text)
