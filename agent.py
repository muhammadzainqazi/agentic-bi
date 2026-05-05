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
from datasources import (query_source, get_source_schema,
                         list_sources, register_source,
                         auto_detect_sources)

load_dotenv()

DATA_PATH = "data/claims.parquet"
NPX = r"C:\Program Files\nodejs\npx.cmd"

# ── MCP Helper ───────────────────────────────────────────────

def _get_pbi_connection(preferred_conn_string: str = None):
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
    ALWAYS call this first before writing any DAX query."""
    try:
        conn_name, proc, send = _get_pbi_connection(
            preferred_conn_string=connection_string if connection_string else None
        )
        if not conn_name:
            return json.dumps({"error": "Power BI Desktop not found. Please open a .pbix file."})

        def get_text(resp):
            content = resp.get("result", {}).get("content", [{}])
            text = content[0].get("text", "{}") if content else "{}"
            try:
                return json.loads(text)
            except:
                return {}

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
            raw = col_result.get("data", [])

# API returns columns nested inside first item
            if raw and isinstance(raw, list) and "columns" in raw[0]:
             cols = raw[0].get("columns", [])
            else:
                cols = raw

            schema[tname] = [
                 {"name": c.get("name"), "dataType": c.get("dataType")}
            for c in cols
            if c.get("name") and not c.get("isHidden", False)
            ]

        proc.kill()
        return json.dumps(schema, indent=2)

    except Exception as e:
        return json.dumps({"error": str(e)})

@tool
def query_powerbi_data(dax_query: str, connection_string: str = "") -> str:
    """Runs a DAX query against the live Power BI Desktop semantic model.
    connection_string: optional — pass if multiple PBI files are open.
    Always use EVALUATE at the start.
    Always use SUMMARIZECOLUMNS for grouped results.
    Use EXACTLY the table/column names from get_powerbi_schema()."""
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
    """Takes actual query results JSON and returns plain English insights.
    data_json must be the real JSON string returned by query_powerbi_data."""
    try:
        parsed = json.loads(data_json)
        rows = parsed.get("data", parsed) if isinstance(parsed, dict) else parsed
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
    data_json must be the real JSON string returned by query_powerbi_data.
    chart_type: bar, line, pie, horizontal_bar"""
    try:
        os.makedirs("output", exist_ok=True)
        parsed = json.loads(data_json)
        rows = parsed.get("data", parsed) if isinstance(parsed, dict) else parsed
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
    measures_json: JSON array of measures with name, expression, formatString, description.
    connection_string: optional — pass if multiple PBI files are open.
    Example: [{"name": "Total Claims", "expression": "SUM('dataset'[claims_count])", "formatString": "#,##0"}]"""
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

        # Auto-detect table name from schema
        schema_raw = get_powerbi_schema(connection_string)
        schema_result = json.loads(schema_raw)
        tables = [k for k in schema_result.keys()
                  if not k.startswith("Date") and not k.startswith("Local")]
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
@tool
def list_data_sources() -> str:
    """Lists all registered data sources available to query.
    Always call this first if the user mentions a specific dataset
    or if Power BI Desktop is not open."""
    try:
        sources = list_sources()
        result = []
        for name, config in sources.items():
            result.append({
                "name": name,
                "type": config.get("type"),
                "description": config.get("description", ""),
                "path": config.get("path", config.get("connection_string", ""))
            })
        return json.dumps(result, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})

@tool
def get_data_source_schema(source_name: str) -> str:
    """Gets the schema of a registered data source.
    source_name: name from list_data_sources()
    Returns columns and data types."""
    try:
        schema = get_source_schema(source_name)
        return json.dumps(schema, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})

@tool
def query_data_source(source_name: str, sql: str) -> str:
    """Queries a registered data source using SQL.
    source_name: name from list_data_sources()
    sql: SQL query — use FROM claims or FROM dataset as table placeholder,
    the engine will replace it with the actual source automatically.

    SQL rules:
    - ALWAYS use GROUP BY with aggregate functions: SUM(), AVG(), COUNT()
    - For ratios: SUM(col1) * 1.0 / NULLIF(SUM(col2), 0)
    - If query fails, read error and fix SQL then retry"""
    try:
        df = query_source(source_name, sql)
        df = df.round(4)
        return json.dumps({
            "success": True,
            "data": df.to_dict(orient="records"),
            "rows": len(df),
            "columns": list(df.columns)
        }, indent=2)
    except Exception as e:
        return json.dumps({
            "error": str(e),
            "tip": "Check SQL aggregation — wrap numeric columns in SUM()"
        })

@tool
def register_data_source(name: str, source_type: str,
                         path: str = "",
                         connection_string: str = "",
                         description: str = "") -> str:
    """Registers a new data source so the agent can query it.
    name: friendly name for the source
    source_type: duckdb, csv, excel, sqlserver
    path: file path for duckdb/csv/excel sources
    connection_string: for SQL Server e.g.
      'DRIVER={SQL Server};SERVER=myserver;DATABASE=mydb;UID=user;PWD=pass'
    description: optional description"""
    try:
        kwargs = {"description": description}
        if path:
            kwargs["path"] = path
        if connection_string:
            kwargs["connection_string"] = connection_string
        sources = register_source(name, source_type, **kwargs)
        return json.dumps({
            "success": True,
            "message": f"Source '{name}' registered as {source_type}",
            "all_sources": list(sources.keys())
        })
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})

# ── All tools ────────────────────────────────────────────────
ALL_TOOLS =  [
    get_powerbi_schema,
    query_powerbi_data,
    summarize_results,
    generate_report,
    create_powerbi_semantic_model,
    list_data_sources,
    get_data_source_schema,
    query_data_source,
    register_data_source
]

# ── State ────────────────────────────────────────────────────
class State(TypedDict):
    messages: Annotated[list, add_messages]
    thread_id: str
    model: str

# ── LLM ─────────────────────────────────────────────────────
def get_llm(model: str = "llama-3.3-70b-versatile"):
    return ChatGroq(
        model=model,
        api_key=os.getenv("GROQ_API_KEY")
    ).bind_tools(ALL_TOOLS)

# ── System prompt ────────────────────────────────────────────
def build_system_prompt(thread_id: str) -> str:
    ctx = get_context(thread_id)
    history = get_history(thread_id, limit=3)
    selected_conn = os.getenv("SELECTED_PBI_CONN", "")

    base ="""You are an expert BI analyst agent with two capabilities:

CAPABILITY 1 — DATA QUERIES (use DuckDB/local sources):
For ANY data question, follow these steps ONE AT A TIME:
Step 1: Call list_data_sources() to see available datasets
Step 2: Call get_data_source_schema() with the source name to get columns
Step 3: Call query_data_source() with correct SQL — wait for actual result
Step 4: Call summarize_results() with the ACTUAL JSON from Step 3
Step 5: Call generate_report() with the ACTUAL JSON from Step 3

CAPABILITY 2 — POWER BI SEMANTIC MODEL (use MCP):
Only when user explicitly asks to create measures, relationships, or model objects:
Step 1: Call get_powerbi_schema() to check what's in the open PBIX
Step 2: Call create_powerbi_semantic_model() with measures JSON array

CRITICAL SQL rules for query_data_source:
- Use FROM claims or FROM dataset as table placeholder
- ALWAYS use GROUP BY with aggregate functions: SUM(), AVG(), COUNT()
- For ratios: SUM(col1) * 1.0 / NULLIF(SUM(col2), 0)
- If query fails, fix SQL and retry immediately

Chart type: comparisons→horizontal_bar, trends→line, proportions→pie, default→bar

NEVER try to query Power BI for data — use list_data_sources and query_data_source instead.
Power BI MCP is ONLY for creating measures and semantic model objects."""

    if selected_conn:
        base += f"""

POWER BI CONNECTION:
- Selected: {selected_conn}
- Pass as connection_string to all PBI tool calls"""

    if ctx.get("last_question"):
        base += f"""

CONVERSATION CONTEXT:
- Last question: {ctx.get('last_question')}
- Last DAX: {ctx.get('last_sql')}
- Last filters: {ctx.get('last_filters') or 'none'}

For follow-up questions (now/filter/same/that/also/only):
MODIFY the last DAX instead of starting fresh."""

    if history:
        base += "\n\nRECENT CONVERSATION:\n"
        for h in history[-2:]:
            base += f"{h['role'].upper()}: {h['content'][:150]}\n"

    return base

# ── Agent node ───────────────────────────────────────────────
def agent_node(state: State):
    msgs = state["messages"]
    thread_id = state.get("thread_id", "default")
    model = state.get("model", "llama-3.3-70b-versatile")
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