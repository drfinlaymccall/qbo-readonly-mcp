import json, os, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import qbo_mcp as q

store = {"client_id": "cid", "client_secret": "sec", "refresh_token": "r1", "realm_id": "999"}
q.kc_get = lambda a: store.get(a)
q.kc_set = lambda a, v: store.__setitem__(a, v)
calls = []
REPORT = {
    "Header": {"ReportName": "ProfitAndLoss", "ReportBasis": "Accrual", "StartPeriod": "2026-07-01",
               "EndPeriod": "2026-10-31", "Currency": "AUD", "Time": "2026-11-17T10:00:00+11:00"},
    "Columns": {"Column": [{"ColTitle": "", "ColType": "Account"}, {"ColTitle": "Total", "ColType": "Money"}]},
    "Rows": {"Row": [
        {"type": "Section", "Header": {"ColData": [{"value": "Income"}, {"value": ""}]},
         "Rows": {"Row": [{"type": "Data", "ColData": [{"value": "Sales"}, {"value": "100.00"}]},
                          {"type": "Data", "ColData": [{"value": "Other income"}, {"value": "50.00"}]}]},
         "Summary": {"ColData": [{"value": "Total Income"}, {"value": "150.00"}]}},
        {"type": "Section", "Summary": {"ColData": [{"value": "Net Income"}, {"value": "150.00"}]}},
    ]},
}

def fake_http(req):
    calls.append((req.get_method(), req.full_url))
    if req.full_url == q.TOKEN_URL:
        return {"access_token": "a1", "expires_in": 3600, "refresh_token": "r2", "x_refresh_token_expires_in": 8640000}
    assert req.get_method() == "GET"
    if "/reports/" in req.full_url:
        return REPORT
    if "/query" in req.full_url:
        return {"QueryResponse": {"Customer": [{"Id": "1"}, {"Id": "2"}], "startPosition": 1, "maxResults": 2}}
    if "companyinfo" in req.full_url:
        return {"CompanyInfo": {"CompanyName": "Example Pty Ltd", "Country": "AU", "CompanyAddr": {"City": "Example City"}}}
q._http = fake_http
q.EXPORT_DIR = tempfile.mkdtemp()

r = q.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
assert r["result"]["serverInfo"]["name"] == "qbo-readonly"
assert q.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
assert len(q.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]) == 4
r = q.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "qbo_report", "arguments": {
    "report": "ProfitAndLoss", "start_date": "2026-07-01", "end_date": "2026-10-31", "summarize_column_by": "Month", "save_csv": True}}})
txt = r["result"]["content"][0]["text"]; print(txt); assert not r["result"]["isError"]
assert "TOTAL INCOME" in txt and "  Sales | 100.00" in txt
assert store["refresh_token"] == "r2", "rotated token not saved"
assert "summarize_column_by=Month" in calls[-1][1] and "minorversion=75" in calls[-1][1]
csvp = txt.split("Saved full report to: ")[1].split("\n")[0]; print(open(csvp, encoding="utf-8-sig").read())
second_csv = q.save_report_csv(REPORT, "ProfitAndLoss")
assert second_csv != csvp and second_csv.endswith("_v2.csv")
for bad in ["DELETE FROM Invoice", "SELECT * FROM Invoice; DELETE", "select * from x where a='update'"]:
    r = q.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "qbo_query", "arguments": {"sql": bad}}})
    assert r["result"]["isError"], bad
r = q.handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "qbo_query", "arguments": {"sql": "SELECT * FROM Customer"}}})
assert not r["result"]["isError"] and '"Id": "2"' in r["result"]["content"][0]["text"]
r = q.handle({"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "qbo_get", "arguments": {"entity": "Invoice", "entity_id": "1 or 1"}}})
assert r["result"]["isError"]
r = q.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "qbo_report", "arguments": {"report": "Nope"}}})
assert r["result"]["isError"]
print(q.handle({"jsonrpc": "2.0", "id": 8, "method": "tools/call", "params": {"name": "qbo_company_info", "arguments": {}}})["result"]["content"][0]["text"])
posts = [c for c in calls if c[0] == "POST"]
assert all(u == q.TOKEN_URL for _, u in posts)
print("methods used:", sorted(set(m for m, _ in calls)))

# stdio round trip with the real process
p = subprocess.run([sys.executable, os.path.join(HERE, "qbo_mcp.py")], input="\n".join(json.dumps(m) for m in [
    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}},
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "qbo_company_info", "arguments": {}}},
]) + "\n", capture_output=True, text=True, env={**os.environ, "QBO_SECRET_BACKEND": "env", "QBO_REALM_ID": ""})
lines = p.stdout.strip().split("\n"); print(len(lines), "replies;", json.loads(lines[2])["result"]["content"][0]["text"])
assert len(lines) == 3
print("ALL OK")
