import subprocess
import json

NPX = r"C:\Program Files\nodejs\npx.cmd"
CONNECTION_STRING = "Data Source=localhost:57836;Application Name=MCP-PBIModeling"

def send_mcp(proc, message):
    proc.stdin.write(json.dumps(message) + "\n")
    proc.stdin.flush()
    while True:
        response = proc.stdout.readline()
        if not response:
            return {"error": "no response"}
        response = response.strip()
        if response.startswith("{"):
            try:
                return json.loads(response)
            except:
                continue

def get_text(resp):
    content = resp.get("result", {}).get("content", [{}])
    text = content[0].get("text", "{}") if content else "{}"
    try:
        return json.loads(text)
    except:
        return {"raw": text}

proc = subprocess.Popen(
    [NPX, "-y", "@microsoft/powerbi-modeling-mcp@latest", "--start"],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    cwd="C:/Users/HP/agentic-bi",
    bufsize=1
)

# Initialize
send_mcp(proc, {
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1.0"}
    }
})
proc.stdin.write(json.dumps({
    "jsonrpc": "2.0",
    "method": "notifications/initialized",
    "params": {}
}) + "\n")
proc.stdin.flush()

# Connect
resp = send_mcp(proc, {
    "jsonrpc": "2.0", "id": 2,
    "method": "tools/call",
    "params": {
        "name": "connection_operations",
        "arguments": {"request": {
            "operation": "Connect",
            "connectionString": CONNECTION_STRING
        }}
    }
})
result = get_text(resp)
conn_name = result.get("data", "")
print(f"Connected: {conn_name}")

measures = [
    {
        "tableName": "dataset",
        "name": "Total Claims",
        "expression": "SUM('dataset'[claims_count])",
        "formatString": "#,##0",
        "description": "Total claims submitted"
    },
    {
        "tableName": "dataset",
        "name": "Approved Claims",
        "expression": "SUM('dataset'[approved_claims])",
        "formatString": "#,##0",
        "description": "Total approved claims"
    },
    {
        "tableName": "dataset",
        "name": "Total Amount AED",
        "expression": "SUM('dataset'[total_amount_aed])",
        "formatString": "AED #,##0",
        "description": "Total claim amount in AED"
    },
    {
        "tableName": "dataset",
        "name": "Approval Rate",
        "expression": "DIVIDE(SUM('dataset'[approved_claims]), SUM('dataset'[claims_count]))",
        "formatString": "0.00%",
        "description": "Ratio of approved to total claims"
    },
    {
        "tableName": "dataset",
        "name": "Avg Claim Value AED",
        "expression": "DIVIDE(SUM('dataset'[total_amount_aed]), SUM('dataset'[claims_count]))",
        "formatString": "AED #,##0.00",
        "description": "Average value per claim"
    }
]

# Create each measure individually so we can see exact errors
for i, m in enumerate(measures):
    print(f"\nCreating: {m['name']}...")
    resp = send_mcp(proc, {
        "jsonrpc": "2.0", "id": i + 3,
        "method": "tools/call",
        "params": {
            "name": "measure_operations",
            "arguments": {"request": {
                "operation": "Create",
                "connectionName": conn_name,
                "definitions": [m],
                "options": {"continueOnError": True}
            }}
        }
    })
    result = get_text(resp)
    print(f"  Success: {result.get('success')}")
    print(f"  Message: {result.get('message', '')}")
    if not result.get('success'):
        print(f"  Full response: {json.dumps(result, indent=2)}")

proc.kill()