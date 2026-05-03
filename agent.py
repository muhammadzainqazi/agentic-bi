import os
import json
import duckdb
import pandas as pd
from dotenv import load_dotenv
from langchain_groq import ChatGroq
from langchain_core.tools import tool
from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.checkpoint.memory import MemorySaver
from typing import Annotated
from typing_extensions import TypedDict
import subprocess
from memory import save_context, get_context, save_message, get_history

load_dotenv()

DATA_PATH = "data/claims.parquet"
NPX = r"C:\Program Files\nodejs\npx.cmd"

# ── MCP Helper ───────────────────────────────────────────────

def _get_pbi_connection(preferred_conn_string: str = None):
    """Auto-detects running Power BI Desktop instance and connects.
    If preferred_conn_string is provided, connects to that specific file.
    Otherwise connects to the first detected instance."""
    proc = subprocess.Popen(
        [NPX, "-y", "@microsoft/powerbi-modeling-mcp@latest", "--start"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd="C:/Users/HP/agentic-bi",
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

    def get_text(resp):
        content = resp.get("result", {}).get("content", [{}])
        text = content[0].get("text", "{}") if content else "{}"
        try:
            return json.loads(text)
        except:
            return {}

    # Initialize
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

    # Use preferred connection or auto-detect
    if preferred_conn_string:
        live_conn_string = preferred_conn_string
    else:
        resp = send(proc, {
            "jsonrpc": "2.0", "id": 2,
            "method": "tools/call",
            "params": {
                "name": "connection_operations",
                "arguments": {"request": {"operation": "ListLocalInstances"}}
            }
        })
        result = get_text(resp)
        instances = result.get("data", [])

        if not instances:
            proc.kill()
            return None, None, None

        live_conn_string = instances[0].get("connectionString")

    # Connect
    resp = send(proc, {
        "jsonrpc": "2.0", "id": 3,
        "method": "tools/call",
        "params": {
            "name": "connection_operations",
            "arguments": {"request": {
                "operation": "Connect",
                "connectionString": live_conn_string
            }}
        }
    })
    result = get_text(resp)
    conn_name = result.get("data", "")

    if not conn_name:
        proc.kill()
        return None, None, None

    return conn_name, proc, send

# ── Tools ────────────────────────────────────────────────────

@tool
def get_powerbi_schema(connection_string: str = "") -> str:
    """Gets the schema of the live Power BI Desktop semantic model.
    Returns all tables and columns currently loaded in the open PBIX file.
    connection_string: optional — pass if multiple PBI files are open.
    Always call this first before writing any DAX query."""
    try:
        conn_name, proc, send = _get_pbi_connection(
            preferred_conn_string=connection_string if connection_string else None
        )

        if not conn_name:
            return json.dumps({"error": "Power BI Desktop not found."})

        def get_text(resp):
            content = resp.get("result", {}).get("content", [{}])
            text = content[0].get("text", "{}") if content else "{}"
            try:
                return json.loads(text)
            except:
                return {}

        # List all tables
        resp = send(proc, {
            "jsonrpc": "2.0", "id": 10,
            "method": "tools/call",
            "params": {
                "name": "table_operations",
                "arguments": {"request": {
                    "operation": "List",
                    "connectionName": conn_name
                }}
            }
        })
        result = get_text(resp)
        tables = result.get("data", [])

        schema = {}
        for table in tables:
            tname = table.get("name", "")
            if tname.startswith("DateTableTemplate") or tname.startswith("LocalDateTable"):
                continue

            resp = send(proc, {
                "jsonrpc": "2.0", "id": 11,
                "method": "tools/call",
                "params": {
                    "name": "column_operations",
                    "arguments": {"request": {
                        "operation": "List",
                        "connectionName": conn_name,
                        "filter": {"tableNames": [tname]}
                    }}
                }
            })
            col_result = get_text(resp)
            cols = col_result.get("data", [])
            schema[tname] = [
                {"name": c.get("name"), "dataType": c.get("dataType")}
                for c in cols
            ]

        proc.kill()
        return json.dumps(schema, indent=2)

    except Exception as e:
        return json.dumps({"error": str(e)})

@tool
def query_powerbi_data(dax_query: str, connection_string: str = "") -> str:
    """Runs a DAX query against the live Power BI Desktop semantic model.
    Use this instead of SQL — queries whatever is loaded in Power BI Desktop.
    connection_string: optional — pass if multiple PBI files are open.

    DAX query examples:
    - EVALUATE SUMMARIZECOLUMNS('dataset'[facility], "Total Claims", SUM('dataset'[claims_count]))
    - EVALUATE SUMMARIZECOLUMNS('dataset'[specialty], "Approved", SUM('dataset'[approved_claims]))
    - EVALUATE ROW("Total", SUM('dataset'[claims_count]))

    Always use EVALUATE at the start.
    Always use SUMMARIZECOLUMNS for grouped results."""
    try:
        conn_name, proc, send = _get_pbi_connection(
            preferred_conn_string=connection_string if connection_string else None
        )

        if not conn_name:
            return json.dumps({"error": "Power BI Desktop not found."})

        def get_text(resp):
            content = resp.get("result", {}).get("content", [{}])
            text = content[0].get("text", "{}") if content else "{}"
            try:
                return json.loads(text)
            except:
                return {"raw": text}

        resp = send(proc, {
            "jsonrpc": "2.0", "id": 10,
            "method": "tools/call",
            "params": {
                "name": "dax_query_operations",
                "arguments": {"request": {
                    "operation": "Execute",
                    "connectionName": conn_name,
                    "query": dax_query
                }}
            }
        })
        result = get_text(resp)
        proc.kill()

        if result.get("success"):
            return json.dumps({"success": True, "data": result.get("data", [])})
        return json.dumps({
            "error": result.get("message", "DAX query failed"),
            "raw": str(result)[:300]
        })

    except Exception as e:
        return json.dumps({"error": str(e)})

@tool
def summarize_results(data_json: str, question: str) -> str:
    """Takes query results and returns plain English insights."""
    try:
        rows = json.loads(data_json)
        if not rows or "error" in str(rows):
            return "No data returned or query failed."
        df = pd.DataFrame(rows)
        lines = [f"Results for: '{question}'\n"]
        lines.append(df.to_string(index=False))
        lines.append(f"\nTotal rows: {len(df)}")
        for col in df.select_dtypes(include='number').columns:
            lines.append(
                f"{col} — Min: {df[col].min():.2f}, "
                f"Max: {df[col].max():.2f}, "
                f"Avg: {df[col].mean():.2f}"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"Summary error: {e}"

@tool
def generate_report(data_json: str, question: str, chart_type: str = "bar") -> str:
    """Generates a standalone HTML report with chart and data table.
    chart_type: bar, line, pie, horizontal_bar"""
    try:
        os.makedirs("output", exist_ok=True)
        rows = json.loads(data_json)
        df = pd.DataFrame(rows)
        cols = df.columns.tolist()
        label_col = cols[0]
        value_cols = [c for c in cols[1:] if pd.api.types.is_numeric_dtype(df[c])]

        if not value_cols:
            return json.dumps({"error": "No numeric columns to chart"})

        labels = df[label_col].astype(str).tolist()
        colors = ["#0078D4", "#50E6FF", "#FFB900", "#E74856", "#00B294"]
        datasets = []
        for i, vc in enumerate(value_cols):
            datasets.append({
                "label": vc.replace("_", " ").title(),
                "data": df[vc].tolist(),
                "backgroundColor": colors[i % len(colors)],
                "borderColor": colors[i % len(colors)],
                "borderWidth": 2,
                "fill": False
            })

        chart_js_type = {
            "bar": "bar", "line": "line",
            "pie": "pie", "horizontal_bar": "bar"
        }.get(chart_type, "bar")
        index_axis = '"y"' if chart_type == "horizontal_bar" else '"x"'
        table_headers = "".join(f"<th>{c.replace('_',' ').title()}</th>" for c in cols)
        table_rows = ""
        for _, row in df.iterrows():
            cells = "".join(
                f"<td>{round(v,4) if isinstance(v,float) else v}</td>"
                for v in row
            )
            table_rows += f"<tr>{cells}</tr>"

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>BI Report — {question[:60]}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: 'Segoe UI', sans-serif; background: #f4f6fb; color: #1a1a2e; padding: 32px; }}
  .header {{ background: linear-gradient(135deg, #0078D4, #00B294); color: white; padding: 28px 32px; border-radius: 12px; margin-bottom: 28px; }}
  .header h1 {{ font-size: 22px; font-weight: 600; }}
  .header p {{ opacity: 0.85; margin-top: 6px; font-size: 14px; }}
  .card {{ background: white; border-radius: 12px; padding: 24px; box-shadow: 0 2px 12px rgba(0,0,0,0.07); margin-bottom: 24px; }}
  .card h2 {{ font-size: 16px; font-weight: 600; margin-bottom: 16px; color: #0078D4; }}
  .chart-wrap {{ position: relative; height: 380px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
  th {{ background: #0078D4; color: white; padding: 10px 14px; text-align: left; }}
  td {{ padding: 9px 14px; border-bottom: 1px solid #eef0f5; }}
  tr:nth-child(even) {{ background: #f8faff; }}
  tr:hover {{ background: #eef4ff; }}
  .footer {{ text-align: center; font-size: 12px; color: #888; margin-top: 16px; }}
</style>
</head>
<body>
<div class="header"><h1>📊 BI Report</h1><p>{question}</p></div>
<div class="card"><h2>Chart</h2><div class="chart-wrap"><canvas id="chart"></canvas></div></div>
<div class="card"><h2>Data Table</h2>
  <table><thead><tr>{table_headers}</tr></thead><tbody>{table_rows}</tbody></table>
</div>
<div class="footer">Generated by Agentic BI — Powered by Groq + LangGraph</div>
<script>
new Chart(document.getElementById('chart').getContext('2d'), {{
  type: '{chart_js_type}',
  data: {{ labels: {json.dumps(labels)}, datasets: {json.dumps(datasets)} }},
  options: {{
    responsive: true, maintainAspectRatio: false, indexAxis: {index_axis},
    plugins: {{ legend: {{ position: 'top' }}, tooltip: {{ mode: 'index', intersect: false }} }},
    scales: {{ x: {{ grid: {{ color: '#f0f0f0' }} }}, y: {{ grid: {{ color: '#f0f0f0' }}, beginAtZero: true }} }}
  }}
}});
</script>
</body></html>"""

        path = "output/report.html"
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        return json.dumps({"saved": True, "path": path, "rows": len(df)})

    except Exception as e:
        return json.dumps({"error": str(e)})

@tool
def create_powerbi_semantic_model(measures_json: str, connection_string: str = "") -> str:
    """Creates DAX measures in the open Power BI Desktop file.
    measures_json is a JSON array of measure objects with:
    - name: measure name
    - expression: DAX expression
    - formatString: optional e.g. '#,##0' or '0.00%'
    - description: optional
    connection_string: optional — pass if multiple PBI files are open.
    Example: [{"name": "Total Claims", "expression": "SUM('dataset'[claims_count])", "formatString": "#,##0"}]
    """
    try:
        conn_name, proc, send = _get_pbi_connection(
            preferred_conn_string=connection_string if connection_string else None
        )

        if not conn_name:
            return json.dumps({
                "success": False,
                "message": "Power BI Desktop not found. Please open a .pbix file first."
            })

        def get_text(resp):
            content = resp.get("result", {}).get("content", [{}])
            text = content[0].get("text", "{}") if content else "{}"
            try:
                return json.loads(text)
            except:
                return {}

        measures = json.loads(measures_json)

        # Get table name from schema if not provided
        schema_result = json.loads(get_powerbi_schema(connection_string))
        tables = [k for k in schema_result.keys() if not k.startswith("Date")]
        table_name = tables[0] if tables else "dataset"

        for m in measures:
            if "tableName" not in m:
                m["tableName"] = table_name

        results = []
        for i, measure in enumerate(measures):
            resp = send(proc, {
                "jsonrpc": "2.0", "id": i + 10,
                "method": "tools/call",
                "params": {
                    "name": "measure_operations",
                    "arguments": {"request": {
                        "operation": "Create",
                        "connectionName": conn_name,
                        "definitions": [measure],
                        "options": {"continueOnError": True}
                    }}
                }
            })
            result = get_text(resp)
            results.append({
                "measure": measure["name"],
                "success": result.get("success", False),
                "message": result.get("message", "")
            })

        proc.kill()
        succeeded = sum(1 for r in results if r["success"])
        return json.dumps({
            "success": succeeded > 0,
            "message": f"Created {succeeded}/{len(measures)} measures in Power BI Desktop",
            "details": results
        })

    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})

# ── All tools ────────────────────────────────────────────────
ALL_TOOLS = [
    get_powerbi_schema,
    query_powerbi_data,
    summarize_results,
    generate_report,
    create_powerbi_semantic_model
]

# ── State ────────────────────────────────────────────────────
class State(TypedDict):
    messages: Annotated[list, add_messages]
    thread_id: str
    model: str

# ── LLM ─────────────────────────────────────────────────────
def get_llm(model: str = "llama-3.1-8b-instant"):
    return ChatGroq(
        model=model,
        api_key=os.getenv("GROQ_API_KEY")
    ).bind_tools(ALL_TOOLS)

# ── System prompt ────────────────────────────────────────────
def build_system_prompt(thread_id: str) -> str:
    ctx = get_context(thread_id)
    history = get_history(thread_id, limit=3)

    selected_conn = os.getenv("SELECTED_PBI_CONN", "")

    base = """You are an expert BI analyst agent. You query live data directly
from whatever is open in Power BI Desktop.

ALWAYS follow these steps for every question:
1. Call get_powerbi_schema() to discover tables and columns in the open PBIX
2. Call query_powerbi_data() with a DAX query based on what you found
3. Call summarize_results() to produce plain English insights
4. Call generate_report() to create an HTML report

CRITICAL DAX rules:
- Always start with EVALUATE
- Use SUMMARIZECOLUMNS for grouped results:
  EVALUATE SUMMARIZECOLUMNS('TableName'[Column], "Measure Name", SUM('TableName'[NumericCol]))
- Use ROW for single values:
  EVALUATE ROW("Total", SUM('TableName'[Column]))
- Use EXACTLY the table and column names returned by get_powerbi_schema()
- If query_powerbi_data returns an error, read it carefully, fix the DAX and retry

Chart type: comparisons→horizontal_bar, trends→line, proportions→pie, default→bar

When user asks to create measures in Power BI:
- Call create_powerbi_semantic_model() with relevant measures
- Use table/column names from get_powerbi_schema() in the DAX expressions"""

    if selected_conn:
        base += f"""

POWER BI CONNECTION:
- User selected connection: {selected_conn}
- Pass this as connection_string to get_powerbi_schema, query_powerbi_data,
  and create_powerbi_semantic_model"""

    if ctx.get("last_question"):
        base += f"""

CONVERSATION CONTEXT:
- Last question: {ctx.get('last_question')}
- Last DAX: {ctx.get('last_sql')}
- Last filters: {ctx.get('last_filters') or 'none'}

For follow-up questions (containing 'now', 'filter', 'same', 'that', 'also', 'only'):
MODIFY the last DAX query instead of starting fresh."""

    if history:
        base += "\n\nRECENT CONVERSATION:\n"
        for h in history[-2:]:
            base += f"{h['role'].upper()}: {h['content'][:150]}\n"

    return base

# ── Agent node ───────────────────────────────────────────────
def agent_node(state: State):
    msgs = state["messages"]
    thread_id = state.get("thread_id", "default")
    model = state.get("model", "llama-3.1-8b-instant")
    llm_with_tools = get_llm(model)
    system = SystemMessage(content=build_system_prompt(thread_id))
    filtered = [m for m in msgs if not isinstance(m, SystemMessage)]
    return {"messages": [llm_with_tools.invoke([system] + filtered)]}

def should_continue(state: State):
    last = state["messages"][-1]
    if hasattr(last, "tool_calls") and last.tool_calls:
        return "tools"
    return END

# ── Graph ────────────────────────────────────────────────────
memory = MemorySaver()

def build_graph():
    g = StateGraph(State)
    g.add_node("agent", agent_node)
    g.add_node("tools", ToolNode(ALL_TOOLS))
    g.set_entry_point("agent")
    g.add_conditional_edges("agent", should_continue, {
        "tools": "tools",
        END: END
    })
    g.add_edge("tools", "agent")
    return g.compile(checkpointer=memory)

graph = build_graph()