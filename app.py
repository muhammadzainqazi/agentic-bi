import streamlit as st
import os
import uuid
import json
import webbrowser
from langchain_core.messages import HumanMessage
from agent import graph

st.set_page_config(
    page_title="Agentic BI",
    page_icon="📊",
    layout="wide"
)

# ── Session state ────────────────────────────────────────────
if "thread_id" not in st.session_state:
    st.session_state.thread_id = str(uuid.uuid4())
if "history" not in st.session_state:
    st.session_state.history = []

# ── Sidebar ──────────────────────────────────────────────────
with st.sidebar:
    st.image("https://img.icons8.com/fluency/96/combo-chart.png", width=60)
    st.title("Agentic BI")
    st.caption("Groq · LangGraph · DuckDB · Power BI MCP")
    st.divider()

    st.subheader("💡 Try these")
    questions = [
        "Show total claims by facility",
        "Compare approved vs total claims by specialty",
        "Show monthly trend of total amount in AED",
        "Which facility has the highest approval rate?",
        "Break down claims by specialty and month",
        "Now filter that by Cardiology only",
    ]
    for q in questions:
        if st.button(q, use_container_width=True):
            st.session_state.prefill = q

    st.divider()
    st.subheader("🔧 Active tools")
    st.markdown("""
    - `get_schema` — reads dataset columns
    - `query_data` — runs SQL on DuckDB
    - `summarize_results` — plain English insights
    - `generate_report` — HTML report with chart
    """)

    st.divider()
    if st.button("🔄 New conversation", use_container_width=True):
        st.session_state.thread_id = str(uuid.uuid4())
        st.session_state.history = []
        st.rerun()

    st.caption(f"Session: `{st.session_state.thread_id[:8]}...`")

# ── Main ─────────────────────────────────────────────────────
st.title("📊 Agentic BI Dashboard Builder")
st.caption("Ask a question in plain English — the agent queries data, gives insights, and generates a report.")

# Render chat history
for msg in st.session_state.history:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("report_path") and os.path.exists(msg["report_path"]):
            with open(msg["report_path"], "r", encoding="utf-8") as f:
                html = f.read()
            st.components.v1.html(html, height=600, scrolling=True)

# Input
prefill = st.session_state.pop("prefill", "") if "prefill" in st.session_state else ""
question = st.chat_input("Ask about your claims data...")
if prefill:
    question = prefill

if question:
    # Show user message
    with st.chat_message("user"):
        st.markdown(question)
    st.session_state.history.append({"role": "user", "content": question})

    # Run agent
    with st.chat_message("assistant"):
        with st.spinner("Agent working..."):
            try:
                config = {"configurable": {"thread_id": st.session_state.thread_id}}
                result = graph.invoke(
                    {"messages": [HumanMessage(content=question)]},
                    config=config
                )

                # Collect tools used
                tools_used = []
                report_path = None
                for msg in result["messages"]:
                    if hasattr(msg, "tool_calls"):
                        for tc in msg.tool_calls:
                            tools_used.append(tc["name"])
                    # Check for saved report
                    content = getattr(msg, "content", "")
                    if isinstance(content, str) and '"saved": true' in content.lower():
                        try:
                            data = json.loads(content)
                            if data.get("saved") and data.get("path", "").endswith(".html"):
                                report_path = data["path"]
                        except Exception:
                            pass

                # Show tools used
                if tools_used:
                    unique_tools = list(dict.fromkeys(tools_used))
                    st.caption("🔧 " + " → ".join(f"`{t}`" for t in unique_tools))

                # Show final response
                final = result["messages"][-1]
                response_text = getattr(final, "content", "Done.")
                st.markdown(response_text)

                # Show report inline
                if report_path and os.path.exists(report_path):
                    st.subheader("📄 Generated Report")
                    with open(report_path, "r", encoding="utf-8") as f:
                        html = f.read()
                    st.components.v1.html(html, height=600, scrolling=True)

                    # Download button
                    st.download_button(
                        label="⬇️ Download Report (HTML)",
                        data=html,
                        file_name="bi_report.html",
                        mime="text/html"
                    )

                # Save to history
                st.session_state.history.append({
                    "role": "assistant",
                    "content": response_text,
                    "report_path": report_path
                })

            except Exception as e:
                st.error(f"Agent error: {e}")
                st.session_state.history.append({
                    "role": "assistant",
                    "content": f"Error: {e}"
                })