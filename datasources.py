import os
import json
import duckdb
import pandas as pd
from typing import Optional


# ── Data Source Registry ─────────────────────────────────────
# Each source has a name, type, and connection config
# Types: duckdb, excel, csv, sqlserver

SOURCES_FILE = "data/sources.json"

def load_sources() -> dict:
    """Load registered data sources from disk."""
    os.makedirs("data", exist_ok=True)
    if not os.path.exists(SOURCES_FILE):
        # Default source — our claims parquet
        default = {
            "claims": {
                "type": "duckdb",
                "path": "data/claims.parquet",
                "description": "Healthcare claims dataset"
            }
        }
        save_sources(default)
        return default
    with open(SOURCES_FILE) as f:
        return json.load(f)

def save_sources(sources: dict):
    """Save registered data sources to disk."""
    os.makedirs("data", exist_ok=True)
    with open(SOURCES_FILE, "w") as f:
        json.dump(sources, f, indent=2)

def register_source(name: str, source_type: str, **kwargs) -> dict:
    """Register a new data source.
    source_type: duckdb, excel, csv, sqlserver
    kwargs: path (for file sources), connection_string (for SQL Server)"""
    sources = load_sources()
    sources[name] = {"type": source_type, **kwargs}
    save_sources(sources)
    return sources

def remove_source(name: str) -> dict:
    """Remove a registered data source."""
    sources = load_sources()
    if name in sources:
        del sources[name]
        save_sources(sources)
    return sources

# ── Query Engine ─────────────────────────────────────────────

def query_source(source_name: str, query: str) -> pd.DataFrame:
    """Query a registered data source.
    For duckdb/parquet/csv: query is SQL
    For excel: query is sheet name or SQL via duckdb
    For sqlserver: query is T-SQL"""
    sources = load_sources()

    if source_name not in sources:
        raise ValueError(f"Source '{source_name}' not registered. Available: {list(sources.keys())}")

    source = sources[source_name]
    source_type = source.get("type")

    if source_type == "duckdb":
        return _query_duckdb(source["path"], query)

    elif source_type == "csv":
        return _query_csv(source["path"], query)

    elif source_type == "excel":
        return _query_excel(source["path"], query, source.get("sheet", 0))

    elif source_type == "sqlserver":
        return _query_sqlserver(source["connection_string"], query)

    else:
        raise ValueError(f"Unknown source type: {source_type}")

def get_source_schema(source_name: str) -> dict:
    """Get schema of a registered data source."""
    sources = load_sources()

    if source_name not in sources:
        raise ValueError(f"Source '{source_name}' not found.")

    source = sources[source_name]
    source_type = source.get("type")

    if source_type == "duckdb":
        return _schema_duckdb(source["path"])
    elif source_type == "csv":
        return _schema_csv(source["path"])
    elif source_type == "excel":
        return _schema_excel(source["path"], source.get("sheet", 0))
    elif source_type == "sqlserver":
        return _schema_sqlserver(source["connection_string"])
    else:
        return {}

# ── DuckDB / Parquet ─────────────────────────────────────────

def _query_duckdb(path: str, sql: str) -> pd.DataFrame:
    conn = duckdb.connect()
    # Auto-replace table placeholder with actual path
    if "FROM claims" in sql.upper() or "FROM dataset" in sql.upper():
        sql = sql.replace("FROM claims", f"FROM READ_PARQUET('{path}')")
        sql = sql.replace("FROM dataset", f"FROM READ_PARQUET('{path}')")
        sql = sql.replace("from claims", f"FROM READ_PARQUET('{path}')")
        sql = sql.replace("from dataset", f"FROM READ_PARQUET('{path}')")
    elif "READ_PARQUET" not in sql:
        sql = sql.replace("FROM ", f"FROM READ_PARQUET('{path}') AS t -- ")
    return conn.execute(sql).df()

def _schema_duckdb(path: str) -> dict:
    conn = duckdb.connect()
    df = conn.execute(f"DESCRIBE SELECT * FROM READ_PARQUET('{path}')").df()
    return {
        "columns": df[["column_name", "column_type"]].to_dict(orient="records"),
        "row_count": conn.execute(f"SELECT COUNT(*) FROM READ_PARQUET('{path}')").fetchone()[0]
    }

# ── CSV ──────────────────────────────────────────────────────

def _query_csv(path: str, sql: str) -> pd.DataFrame:
    conn = duckdb.connect()
    sql = sql.replace("FROM claims", f"FROM READ_CSV_AUTO('{path}')")
    sql = sql.replace("FROM dataset", f"FROM READ_CSV_AUTO('{path}')")
    sql = sql.replace("from claims", f"FROM READ_CSV_AUTO('{path}')")
    sql = sql.replace("from dataset", f"FROM READ_CSV_AUTO('{path}')")
    if "READ_CSV" not in sql:
        sql = f"SELECT * FROM READ_CSV_AUTO('{path}') LIMIT 100"
    return conn.execute(sql).df()

def _schema_csv(path: str) -> dict:
    df = pd.read_csv(path, nrows=5)
    return {
        "columns": [{"column_name": c, "column_type": str(df[c].dtype)} for c in df.columns],
        "row_count": sum(1 for _ in open(path)) - 1
    }

# ── Excel ────────────────────────────────────────────────────

def _query_excel(path: str, query: str, sheet=0) -> pd.DataFrame:
    df = pd.read_excel(path, sheet_name=sheet)
    # Register as duckdb in-memory table and run SQL
    conn = duckdb.connect()
    conn.register("excel_data", df)
    sql = query.replace("FROM claims", "FROM excel_data")
    sql = sql.replace("FROM dataset", "FROM excel_data")
    sql = sql.replace("from claims", "FROM excel_data")
    sql = sql.replace("from dataset", "FROM excel_data")
    if "excel_data" not in sql:
        return df
    return conn.execute(sql).df()

def _schema_excel(path: str, sheet=0) -> dict:
    df = pd.read_excel(path, sheet_name=sheet, nrows=5)
    return {
        "columns": [{"column_name": c, "column_type": str(df[c].dtype)} for c in df.columns],
        "sheets": pd.ExcelFile(path).sheet_names
    }

# ── SQL Server ───────────────────────────────────────────────

def _query_sqlserver(connection_string: str, sql: str) -> pd.DataFrame:
    try:
        import pyodbc
        conn = pyodbc.connect(connection_string)
        return pd.read_sql(sql, conn)
    except ImportError:
        raise ImportError("pyodbc not installed. Run: pip install pyodbc")
    except Exception as e:
        raise Exception(f"SQL Server error: {e}")

def _schema_sqlserver(connection_string: str) -> dict:
    try:
        import pyodbc
        conn = pyodbc.connect(connection_string)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE
            FROM INFORMATION_SCHEMA.COLUMNS
            ORDER BY TABLE_NAME, ORDINAL_POSITION
        """)
        rows = cursor.fetchall()
        schema = {}
        for table, col, dtype in rows:
            if table not in schema:
                schema[table] = []
            schema[table].append({"column_name": col, "column_type": dtype})
        return schema
    except Exception as e:
        return {"error": str(e)}

# ── Helpers ──────────────────────────────────────────────────

def list_sources() -> dict:
    """List all registered data sources with their types."""
    return load_sources()

def auto_detect_sources() -> dict:
    """Auto-detect data files in the data/ folder and register them."""
    detected = {}
    data_dir = "data"
    if not os.path.exists(data_dir):
        return detected

    for fname in os.listdir(data_dir):
        fpath = os.path.join(data_dir, fname)
        name = os.path.splitext(fname)[0]

        if fname.endswith(".parquet"):
            detected[name] = {"type": "duckdb", "path": fpath, "description": f"Auto-detected: {fname}"}
        elif fname.endswith(".csv"):
            detected[name] = {"type": "csv", "path": fpath, "description": f"Auto-detected: {fname}"}
        elif fname.endswith((".xlsx", ".xls")):
            detected[name] = {"type": "excel", "path": fpath, "description": f"Auto-detected: {fname}"}

    # Merge with existing
    existing = load_sources()
    existing.update(detected)
    save_sources(existing)
    return existing