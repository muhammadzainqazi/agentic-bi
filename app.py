import streamlit as st
import os
import uuid
import json
from langchain_core.messages import HumanMessage
from agent import graph
from memory import (save_message, save_context, get_context,
                    clear_thread, init_db, get_all_threads, get_history)

init_db()

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
    - `create_powerbi_semantic_model` — creates DAX measures
    """)

    st.divider()
    if st.button("🔄 New conversation", use_container_width=True):
        st.session_state.thread_id = str(uuid.uuid4())
        st.session_state.history = []
        st.rerun()

    st.divider()
    st.subheader("🕐 Past conversations")
    threads = get_all_threads()
    for t in threads[:5]:
        if st.button(
            f"💬 {t['thread_id'][:8]}... ({t['messages']} msgs)",
            key=t['thread_id'],
            use_container_width=True
        ):
            st.session_state.thread_id = t['thread_id']
            st.session_state.history = [
                {"role": h["role"], "content": h["content"]}
                for h in get_history(t['thread_id'])
            ]
            st.rerun()

    st.caption(f"Session: `{st.session_state.thread_id[:8]}...`")
    st.divider()
st.subheader("⚙️ Settings")

# Model selector
model_choice = st.selectbox(
    "LLM Model",
    options=[
        "llama-3.1-8b-instant",
        "llama-3.3-70b-versatile",
        "llama3-70b-8192",
        "mixtral-8x7b-32768"
    ],
    index=0,
    help="Smaller models use fewer tokens but may be less accurate"
)
st.session_state.model_choice = model_choice

# Power BI file selector
st.divider()
st.subheader("📊 Power BI Connection")
if st.button("🔍 Detect open PBI files", use_container_width=True):
    import subprocess
    import json
    NPX = r"C:\Program Files\nodejs\npx.cmd"
    try:
        proc = subprocess.Popen(
            [NPX, "-y", "@microsoft/powerbi-modeling-mcp@latest", "--start"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )
        def send(p, msg):
            p.stdin.write(json.dumps(msg) + "\n")
            p.stdin.flush()
            while True:
                line = p.stdout.readline().strip()
                if line.startswith("{"):
                    try:
                        return json.loads(line)
                    except:
                        continue
                if not line:
                    return {}

        send(proc, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "agentic-bi", "version": "1.0"}
            }
        })
        proc.stdin.write(json.dumps({
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {}
        }) + "\n")
        proc.stdin.flush()

        resp = send(proc, {
            "jsonrpc": "2.0", "id": 2,
            "method": "tools/call",
            "params": {
                "name": "connection_operations",
                "arguments": {"request": {"operation": "ListLocalInstances"}}
            }
        })
        content = resp.get("result", {}).get("content", [{}])
        text = content[0].get("text", "{}") if content else "{}"
        result = json.loads(text)
        instances = result.get("data", [])
        proc.kill()

        if instances:
            st.session_state.pbi_instances = instances
            st.success(f"Found {len(instances)} file(s)")
        else:
            st.warning("No Power BI files detected")
    except Exception as e:
        st.error(f"Detection error: {e}")

# Show file picker if instances found
if "pbi_instances" in st.session_state and st.session_state.pbi_instances:
    instance_options = {
        f"{inst.get('parentWindowTitle', 'Unknown')} (port {inst.get('port')})": inst
        for inst in st.session_state.pbi_instances
    }
    selected_label = st.selectbox(
        "Select Power BI file:",
        options=list(instance_options.keys())
    )
    selected_instance = instance_options[selected_label]
    st.session_state.selected_pbi_conn = selected_instance.get("connectionString")
    st.caption(f"Port: `{selected_instance.get('port')}`")

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
                    {
                        "messages": [HumanMessage(content=question)],
                        "thread_id": st.session_state.thread_id
                    },
                    config=config
                )

                # Collect tools used + extract SQL and report path
                tools_used = []
                report_path = None
                sql_used = ""

                for msg in result["messages"]:
                    if hasattr(msg, "tool_calls"):
                        for tc in msg.tool_calls:
                            tools_used.append(tc["name"])
                            if tc["name"] == "query_data":
                                sql_used = tc["args"].get("sql", "")
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
                    st.download_button(
                        label="⬇️ Download Report (HTML)",
                        data=html,
                        file_name="bi_report.html",
                        mime="text/html"
                    )

                # Save persistent memory
                save_context(
                    st.session_state.thread_id,
                    last_question=question,
                    last_sql=sql_used,
                    last_filters=None
                )
                save_message(st.session_state.thread_id, "user", question)
                save_message(st.session_state.thread_id, "assistant", response_text)

                # Save to session history
                st.session_state.history.append({
                    "role": "assistant",
                    "content": response_text,
                    "report_path": report_path
                })

            except Exception as e:
                error_str = str(e)
                if "429" in error_str or "rate_limit" in error_str.lower():
                    st.warning("⏳ Groq rate limit reached. Wait ~15 minutes and try again.")
                else:
                    st.error(f"Agent error: {e}")
                st.session_state.history.append({
                    "role": "assistant",
                    "content": f"Error: {e}"
                })