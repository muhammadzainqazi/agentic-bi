import subprocess
import json

NPX = r"C:\Program Files\nodejs\npx.cmd"

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

resp = send_mcp(proc, {
    "jsonrpc": "2.0", "id": 2,
    "method": "tools/call",
    "params": {
        "name": "connection_operations",
        "arguments": {"request": {"operation": "ListLocalInstances"}}
    }
})
result = get_text(resp)
instances = result.get("data", [])
print(f"Found {len(instances)} Power BI instance(s):")
for inst in instances:
    print(f"  Port: {inst.get('port')}")
    print(f"  File: {inst.get('parentWindowTitle', 'unknown')}")
    print(f"  Connection: {inst.get('connectionString')}")

proc.kill()