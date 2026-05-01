import os
import re
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

load_dotenv()

DATA_PATH = "data/claims.parquet"

# ── Tools ────────────────────────────────────────────────────

@tool
def get_schema() -> str:
    """Get the schema of the claims dataset.
    Always call this first before writing any SQL."""
    conn = duckdb.connect()
    df = conn.execute(
        f"DESCRIBE SELECT * FROM READ_PARQUET('{DATA_PATH}')"
    ).df()
    return df.to_string(index=False)

@tool
def query_data(sql: str) -> str:
    """Run a DuckDB SQL query against the claims dataset.
    Table is READ_PARQUET('data/claims.parquet') aliased as claims.
    ALWAYS use SUM() AVG() COUNT() with GROUP BY.
    For ratios use: SUM(col1) * 1.0 / NULLIF(SUM(col2), 0)"""
    try:
        conn = duckdb.connect()
        df = conn.execute(sql).df()
        # Round floats
        df = df.round(4)
        return df.to_json(orient="records", indent=2)
    except Exception as e:
        return json.dumps({"error": str(e), "tip": "Check aggregation — wrap numeric columns in SUM()"})

@tool
def summarize_results(data_json: str, question: str) -> str:
    """Takes query results and the original question,
    returns a plain English summary with key numbers and insights."""
    try:
        rows = json.loads(data_json)
        if not rows or "error" in str(rows):
            return "No data returned or query failed."
        df = pd.DataFrame(rows)
        lines = [f"Here are the results for: '{question}'\n"]
        lines.append(df.to_string(index=False))
        lines.append(f"\nTotal rows: {len(df)}")
        # Add basic insights
        for col in df.select_dtypes(include='number').columns:
            lines.append(f"{col} — Min: {df[col].min():.2f}, Max: {df[col].max():.2f}, Avg: {df[col].mean():.2f}")
        return "\n".join(lines)
    except Exception as e:
        return f"Summary error: {e}"

@tool
def generate_report(data_json: str, question: str, chart_type: str = "bar") -> str:
    """Generates a standalone HTML report with chart and data table.
    chart_type can be: bar, line, pie, horizontal_bar
    Saves to output/report.html and returns the path."""
    try:
        os.makedirs("output", exist_ok=True)
        rows = json.loads(data_json)
        df = pd.DataFrame(rows)

        cols = df.columns.tolist()
        label_col = cols[0]
        value_cols = [c for c in cols[1:] if pd.api.types.is_numeric_dtype(df[c])]

        if not value_cols:
            return json.dumps({"error": "No numeric columns to chart"})

        value_col = value_cols[0]
        labels = df[label_col].astype(str).tolist()
        values = df[value_col].tolist()

        # Build chart datasets for all value columns
        datasets = []
        colors = ["#0078D4", "#50E6FF", "#FFB900", "#E74856", "#00B294"]
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
            "bar": "bar",
            "line": "line",
            "pie": "pie",
            "horizontal_bar": "bar"
        }.get(chart_type, "bar")

        index_axis = '"x"' if chart_type != "horizontal_bar" else '"y"'

        # Build table rows
        table_headers = "".join(f"<th>{c.replace('_',' ').title()}</th>" for c in cols)
        table_rows = ""
        for _, row in df.iterrows():
            cells = "".join(f"<td>{round(v, 4) if isinstance(v, float) else v}</td>" for v in row)
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
  .header {{ background: linear-gradient(135deg, #0078D4, #00B294); color: white;
             padding: 28px 32px; border-radius: 12px; margin-bottom: 28px; }}
  .header h1 {{ font-size: 22px; font-weight: 600; }}
  .header p {{ opacity: 0.85; margin-top: 6px; font-size: 14px; }}
  .card {{ background: white; border-radius: 12px; padding: 24px;
           box-shadow: 0 2px 12px rgba(0,0,0,0.07); margin-bottom: 24px; }}
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
<div class="header">
  <h1>📊 BI Report</h1>
  <p>{question}</p>
</div>

<div class="card">
  <h2>Chart</h2>
  <div class="chart-wrap">
    <canvas id="chart"></canvas>
  </div>
</div>

<div class="card">
  <h2>Data Table</h2>
  <table>
    <thead><tr>{table_headers}</tr></thead>
    <tbody>{table_rows}</tbody>
  </table>
</div>

<div class="footer">Generated by Agentic BI — Powered by Groq + LangGraph</div>

<script>
const ctx = document.getElementById('chart').getContext('2d');
new Chart(ctx, {{
  type: '{chart_js_type}',
  data: {{
    labels: {json.dumps(labels)},
    datasets: {json.dumps(datasets)}
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    indexAxis: {index_axis},
    plugins: {{
      legend: {{ position: 'top' }},
      tooltip: {{ mode: 'index', intersect: false }}
    }},
    scales: {{
      x: {{ grid: {{ color: '#f0f0f0' }} }},
      y: {{ grid: {{ color: '#f0f0f0' }}, beginAtZero: true }}
    }}
  }}
}});
</script>
</body>
</html>"""

        path = "output/report.html"
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)

        return json.dumps({"saved": True, "path": path, "rows": len(df)})

    except Exception as e:
        return json.dumps({"error": str(e)})


@tool
def powerbi_mcp(instruction: str) -> str:
    """Sends a natural language instruction to the Power BI Modeling MCP server.
    Use this to create tables, measures, relationships in the open PBIX file.
    Examples:
    - 'Create a measure called Total Claims = SUM(claims[claims_count])'
    - 'Create a measure called Approval Rate = DIVIDE(SUM(claims[approved_claims]), SUM(claims[claims_count]))'
    - 'Create a measure called Total Amount AED = SUM(claims[total_amount_aed])'
    - 'Connect to claims_dashboard'
    """
    try:
        # Build JSON-RPC initialize + instruction
        init_msg = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "agentic-bi", "version": "1.0"}
            }
        })

        initialized_msg = json.dumps({
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {}
        })

        # Connect to PBI Desktop tool call
        connect_msg = json.dumps({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "connect_to_powerbi_desktop",
                "arguments": {"fileName": "claims_dashboard"}
            }
        })

        # The actual instruction as a tool call
        instruction_msg = json.dumps({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "create_measure",
                "arguments": {
                    "tableName": "dataset",
                    "measureName": instruction.split("called")[-1].split("=")[0].strip() if "called" in instruction else "New Measure",
                    "expression": instruction.split("=")[-1].strip() if "=" in instruction else instruction
                }
            }
        })

        input_sequence = "\n".join([
            init_msg,
            initialized_msg,
            connect_msg,
            instruction_msg,
            ""
        ])

        proc = subprocess.Popen(
            ["npx", "-y", "@microsoft/powerbi-modeling-mcp@latest", "--start"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd="C:/Users/HP/agentic-bi"
        )

        stdout, stderr = proc.communicate(input=input_sequence, timeout=60)

        # Parse responses
        responses = []
        for line in stdout.strip().split("\n"):
            line = line.strip()
            if line.startswith("{"):
                try:
                    parsed = json.loads(line)
                    if "result" in parsed:
                        responses.append(parsed["result"])
                except Exception:
                    pass

        if responses:
            return json.dumps({"success": True, "responses": responses})
        return json.dumps({
            "success": False,
            "stdout": stdout[:500],
            "stderr": stderr[:500]
        })

    except subprocess.TimeoutExpired:
        proc.kill()
        return json.dumps({"error": "MCP server timed out after 60s"})
    except Exception as e:
        return json.dumps({"error": str(e)})
NPX = r"C:\Program Files\nodejs\npx.cmd"

def _get_pbi_connection():
    """Gets the active Power BI Desktop port and connection string."""
    proc = subprocess.Popen(
        [NPX, "-y", "@microsoft/powerbi-modeling-mcp@latest", "--start"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd="C:/Users/HP/agentic-bi",
        bufsize=1
    )

    def send(proc, msg):
        proc.stdin.write(json.dumps(msg) + "\n")
        proc.stdin.flush()
        while True:
            line = proc.stdout.readline().strip()
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

    # List instances
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
        return None, None, proc

    port = instances[0].get("port")
    conn_string = f"Data Source=localhost:{port};Application Name=MCP-PBIModeling"

    # Connect
    resp = send(proc, {
        "jsonrpc": "2.0", "id": 3,
        "method": "tools/call",
        "params": {
            "name": "connection_operations",
            "arguments": {"request": {
                "operation": "Connect",
                "connectionString": conn_string
            }}
        }
    })
    result = get_text(resp)
    conn_name = result.get("data", "")

    return conn_name, proc, send

@tool
def create_powerbi_semantic_model(measures_json: str) -> str:
    """Creates DAX measures in the open Power BI Desktop file.
    measures_json is a JSON array of measure objects, each with:
    - name: measure name
    - expression: DAX expression
    - formatString: optional format string
    - description: optional description
    Table name is always 'dataset'.
    Example: [{"name": "Total Claims", "expression": "SUM('dataset'[claims_count])", "formatString": "#,##0"}]
    """
    try:
        conn_name, proc, send = _get_pbi_connection()

        if not conn_name:
            return json.dumps({
                "success": False,
                "message": "Power BI Desktop not found. Make sure it is open with a file loaded."
            })

        def get_text(resp):
            content = resp.get("result", {}).get("content", [{}])
            text = content[0].get("text", "{}") if content else "{}"
            try:
                return json.loads(text)
            except:
                return {}

        measures = json.loads(measures_json)

        # Add tableName to each measure
        for m in measures:
            m["tableName"] = "dataset"

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
    get_schema,
    query_data,
    summarize_results,
    generate_report,
    create_powerbi_semantic_model
]
# ── State ────────────────────────────────────────────────────
class State(TypedDict):
    messages: Annotated[list, add_messages]

# ── LLM ─────────────────────────────────────────────────────
llm = ChatGroq(
    model="llama-3.3-70b-versatile",
    api_key=os.getenv("GROQ_API_KEY")
)
llm_with_tools = llm.bind_tools(ALL_TOOLS)

SYSTEM_PROMPT = """You are an expert BI analyst agent for a UAE healthcare claims dataset.

ALWAYS follow these steps for every question:
1. Call get_schema() to understand available columns
2. Call query_data() with correct aggregated SQL
3. Call summarize_results() to produce plain English insights
4. Call generate_report() to create an HTML report with chart and table

If the user asks to create a semantic model, measures, or anything in Power BI:
5. Call powerbi_mcp() for EACH measure you want to create
   - First create: Total Claims = SUM(claims[claims_count])
   - Then: Approved Claims = SUM(claims[approved_claims])
   - Then: Total Amount AED = SUM(claims[total_amount_aed])
   - Then: Approval Rate = DIVIDE(SUM(claims[approved_claims]), SUM(claims[claims_count]))
   - Then: Avg Claim Value = DIVIDE(SUM(claims[total_amount_aed]), SUM(claims[claims_count]))

CRITICAL SQL rules:
- Table: FROM READ_PARQUET('data/claims.parquet') AS claims
- ALWAYS use GROUP BY with aggregate functions: SUM(), AVG(), COUNT()
- For ratios: SUM(col1) * 1.0 / NULLIF(SUM(col2), 0)
- If query_data returns an error, fix SQL and retry

Chart type selection:
- Comparisons → horizontal_bar
- Trends over time → line
- Proportions → pie
- Default → bar
When user asks to create a semantic model, measures, or anything in Power BI:
- Call create_powerbi_semantic_model() with a JSON array of measures
- Always include these 5 core measures:
  [
    {"name": "Total Claims", "expression": "SUM('dataset'[claims_count])", "formatString": "#,##0"},
    {"name": "Approved Claims", "expression": "SUM('dataset'[approved_claims])", "formatString": "#,##0"},
    {"name": "Total Amount AED", "expression": "SUM('dataset'[total_amount_aed])", "formatString": "AED #,##0"},
    {"name": "Approval Rate", "expression": "DIVIDE(SUM('dataset'[approved_claims]), SUM('dataset'[claims_count]))", "formatString": "0.00%"},
    {"name": "Avg Claim Value AED", "expression": "DIVIDE(SUM('dataset'[total_amount_aed]), SUM('dataset'[claims_count]))", "formatString": "AED #,##0.00"}
  ]
You remember the conversation — build on previous questions when user says 'filter by X'."""

memory = MemorySaver()

# ── Nodes ────────────────────────────────────────────────────
def agent_node(state: State):
    msgs = state["messages"]
    if not any(isinstance(m, SystemMessage) for m in msgs):
        msgs = [SystemMessage(content=SYSTEM_PROMPT)] + msgs
    return {"messages": [llm_with_tools.invoke(msgs)]}

def should_continue(state: State):
    last = state["messages"][-1]
    if hasattr(last, "tool_calls") and last.tool_calls:
        return "tools"
    return END

# ── Graph ────────────────────────────────────────────────────
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