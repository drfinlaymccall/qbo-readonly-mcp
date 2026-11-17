#!/usr/bin/env python3
"""QuickBooks Online: read-only MCP server and command line tool.

Design rules:
- READ ONLY. The only HTTP POST this file can make is to Intuit's token endpoint.
  Every data call goes through api_get(), which only issues GET requests.
  The query tool accepts SELECT statements only.
- Standard library only, so it runs on a stock Python 3.8+ with nothing to
  install and nothing to keep up to date.
- Secrets (client id, client secret, refresh token, realm id) live in the
  operating system's own encrypted store (macOS Keychain, Windows Credential
  Manager, or the Linux Secret Service), never in a synced or shared folder.

Usage:
  python3 qbo_mcp.py setup                 store credentials in the OS secret store
  python3 qbo_mcp.py test                  check the connection (company info)
  python3 qbo_mcp.py report NAME [START END] [key=value ...]
  python3 qbo_mcp.py query "SELECT * FROM Customer"
  python3 qbo_mcp.py                       run as an MCP server over stdio
"""
import base64
import csv
import datetime
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

VERSION = "1.1.0"
SERVER_NAME = "qbo-readonly"
KEYCHAIN_SERVICE = os.environ.get("QBO_KEYCHAIN_SERVICE", "qbo-readonly")
API_BASE = os.environ.get("QBO_API_BASE", "https://quickbooks.api.intuit.com")
if API_BASE not in {"https://quickbooks.api.intuit.com", "https://sandbox-quickbooks.api.intuit.com"}:
    raise ValueError("QBO_API_BASE must be an Intuit production or sandbox endpoint.")
TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
MINOR_VERSION = "75"
EXPORT_DIR = os.environ.get(
    "QBO_EXPORT_DIR",
    os.path.expanduser("~/Documents/QBO Exports"),
)
TIMEOUT = 60

# Reports this server will run. Names are the QBO API report endpoints.
REPORTS = {
    "ProfitAndLoss": "Profit and Loss",
    "ProfitAndLossDetail": "Profit and Loss Detail",
    "BalanceSheet": "Balance Sheet",
    "CashFlow": "Statement of Cash Flows",
    "TrialBalance": "Trial Balance",
    "GeneralLedger": "General Ledger",
    "TransactionList": "Transaction List",
    "CustomerSales": "Sales by Customer Summary",
    "CustomerIncome": "Income by Customer Summary",
    "ItemSales": "Sales by Product/Service Summary",
    "ClassSales": "Sales by Class Summary",
    "DepartmentSales": "Sales by Location Summary",
    "CustomerBalance": "Customer Balance Summary",
    "CustomerBalanceDetail": "Customer Balance Detail",
    "AgedReceivables": "Aged Receivables Summary",
    "AgedReceivableDetail": "Aged Receivables Detail",
    "AgedPayables": "Aged Payables Summary",
    "AgedPayableDetail": "Aged Payables Detail",
    "VendorBalance": "Supplier Balance Summary",
    "VendorExpenses": "Expenses by Supplier Summary",
    "AccountList": "Account List",
}

# Entities the get tool may read.
ENTITIES = {
    "Account", "Bill", "BillPayment", "Class", "CompanyInfo", "CreditMemo",
    "Customer", "Department", "Deposit", "Employee", "Estimate", "Invoice",
    "Item", "JournalEntry", "Payment", "PaymentMethod", "Preferences",
    "Purchase", "PurchaseOrder", "RefundReceipt", "SalesReceipt", "TaxCode",
    "TaxRate", "Term", "Transfer", "Vendor", "VendorCredit",
}

_access_token = None
_access_expiry = None


if sys.platform == "win32":
    _DEFAULT_LOG = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "qbo-readonly", "qbo-readonly.log")
elif sys.platform == "darwin":
    _DEFAULT_LOG = os.path.expanduser("~/Library/Logs/qbo-readonly.log")
else:
    _DEFAULT_LOG = os.path.expanduser("~/.local/state/qbo-readonly/qbo-readonly.log")
LOG_FILE = os.environ.get("QBO_LOG_FILE", _DEFAULT_LOG)


def log(msg):
    line = "%s [%s] %s" % (datetime.datetime.now().isoformat(timespec="seconds"), SERVER_NAME, msg)
    sys.stderr.write(line + "\n")
    sys.stderr.flush()
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------- Secret storage
#
# Credentials live in the operating system's own encrypted store:
#   macOS    Keychain, through the built-in `security` command
#   Windows  Credential Manager (DPAPI encrypted, tied to the Windows login),
#            through advapi32 CredReadW / CredWriteW via ctypes
#   Linux    Secret Service (GNOME Keyring or KWallet), through `secret-tool`
# QBO_SECRET_BACKEND can force one: keychain, wincred, secret-tool or env.
# The env backend reads QBO_CLIENT_ID, QBO_CLIENT_SECRET, QBO_REALM_ID and
# QBO_REFRESH_TOKEN and cannot save a rotated refresh token, so it is for
# testing only.

def _backend():
    b = os.environ.get("QBO_SECRET_BACKEND")
    if b:
        return b
    if sys.platform == "darwin":
        return "keychain"
    if sys.platform == "win32":
        return "wincred"
    return "secret-tool"


def _env_get(account):
    return os.environ.get("QBO_" + account.upper())


# macOS Keychain
def _keychain_get(account):
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", account, "-w"],
            capture_output=True, text=True, check=False,
        )
    except FileNotFoundError:
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def _keychain_set(account, value):
    r = subprocess.run(
        ["security", "add-generic-password", "-U", "-s", KEYCHAIN_SERVICE, "-a", account, "-w", value],
        capture_output=True, text=True, check=False,
    )
    if r.returncode != 0:
        raise RuntimeError("Could not write %s to the Keychain: %s" % (account, r.stderr.strip()))


# Windows Credential Manager
def _wincred_api():
    import ctypes
    from ctypes import wintypes

    class CREDENTIAL(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi32.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                   ctypes.POINTER(ctypes.POINTER(CREDENTIAL))]
    advapi32.CredReadW.restype = wintypes.BOOL
    advapi32.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIAL), wintypes.DWORD]
    advapi32.CredWriteW.restype = wintypes.BOOL
    advapi32.CredFree.argtypes = [ctypes.c_void_p]
    advapi32.CredFree.restype = None
    return ctypes, CREDENTIAL, advapi32


_CRED_TYPE_GENERIC = 1
_CRED_PERSIST_LOCAL_MACHINE = 2
_ERROR_NOT_FOUND = 1168


def _wincred_target(account):
    return "%s:%s" % (KEYCHAIN_SERVICE, account)


def _wincred_get(account):
    ctypes, CREDENTIAL, advapi32 = _wincred_api()
    pcred = ctypes.POINTER(CREDENTIAL)()
    if not advapi32.CredReadW(_wincred_target(account), _CRED_TYPE_GENERIC, 0, ctypes.byref(pcred)):
        err = ctypes.get_last_error()
        if err == _ERROR_NOT_FOUND:
            return None
        raise RuntimeError("Could not read %s from Windows Credential Manager (error %d)" % (account, err))
    try:
        cred = pcred.contents
        data = ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
        return data.decode("utf-8")
    finally:
        advapi32.CredFree(pcred)


def _wincred_set(account, value):
    ctypes, CREDENTIAL, advapi32 = _wincred_api()
    blob = value.encode("utf-8")
    if len(blob) > 2560:
        raise RuntimeError("%s is too long for Windows Credential Manager" % account)
    buf = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
    cred = CREDENTIAL()
    cred.Type = _CRED_TYPE_GENERIC
    cred.TargetName = _wincred_target(account)
    cred.CredentialBlobSize = len(blob)
    cred.CredentialBlob = ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte))
    cred.Persist = _CRED_PERSIST_LOCAL_MACHINE
    cred.UserName = account
    if not advapi32.CredWriteW(ctypes.byref(cred), 0):
        raise RuntimeError("Could not write %s to Windows Credential Manager (error %d)"
                           % (account, ctypes.get_last_error()))


# Linux Secret Service
def _secret_tool_get(account):
    try:
        out = subprocess.run(["secret-tool", "lookup", "service", KEYCHAIN_SERVICE, "account", account],
                             capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return None
    return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else None


def _secret_tool_set(account, value):
    try:
        r = subprocess.run(["secret-tool", "store", "--label", "%s %s" % (KEYCHAIN_SERVICE, account),
                            "service", KEYCHAIN_SERVICE, "account", account],
                           input=value, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        raise RuntimeError("secret-tool not found. Install libsecret-tools (Debian/Ubuntu) or libsecret (Fedora).")
    if r.returncode != 0:
        raise RuntimeError("Could not write %s with secret-tool: %s" % (account, r.stderr.strip()))


_GETTERS = {"keychain": _keychain_get, "wincred": _wincred_get, "secret-tool": _secret_tool_get, "env": _env_get}
_SETTERS = {"keychain": _keychain_set, "wincred": _wincred_set, "secret-tool": _secret_tool_set}


def kc_get(account):
    b = _backend()
    if b not in _GETTERS:
        raise RuntimeError("Unknown QBO_SECRET_BACKEND %r" % b)
    return _GETTERS[b](account) or _env_get(account)


def kc_set(account, value):
    b = _backend()
    if b not in _SETTERS:
        raise RuntimeError("The %r secret backend cannot save values; use the operating system store." % b)
    _SETTERS[b](account, value)


# ---------------------------------------------------------------- HTTP

class QboError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _http(req):
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:2000]
        tid = e.headers.get("intuit_tid", "") if e.headers else ""
        msg = "HTTP %s from Intuit (intuit_tid %s): %s" % (e.code, tid or "none", body)
        log("HTTP %s from Intuit" % e.code)
        raise QboError(msg)
    except urllib.error.URLError as e:
        log("ERROR could not reach Intuit: %s" % e.reason)
        raise QboError("Could not reach Intuit: %s" % e.reason)


def refresh_access_token():
    """The one POST in this file. It goes to Intuit's token endpoint only."""
    global _access_token, _access_expiry
    cid, secret, rtok = kc_get("client_id"), kc_get("client_secret"), kc_get("refresh_token")
    if not (cid and secret and rtok):
        raise QboError("Credentials missing. Run: python3 qbo_mcp.py setup")
    basic = base64.b64encode(("%s:%s" % (cid, secret)).encode()).decode()
    data = urllib.parse.urlencode({"grant_type": "refresh_token", "refresh_token": rtok}).encode()
    req = urllib.request.Request(TOKEN_URL, data=data, method="POST", headers={
        "Authorization": "Basic " + basic,
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    })
    try:
        tok = _http(req)
    except QboError as e:
        if "invalid_grant" in str(e):
            raise QboError("QuickBooks access has expired or was disconnected (invalid_grant). "
                           "Get a new refresh token from the OAuth Playground and run: python3 qbo_mcp.py setup")
        raise
    _access_token = tok["access_token"]
    _access_expiry = datetime.datetime.now() + datetime.timedelta(seconds=int(tok.get("expires_in", 3600)) - 120)
    new_rtok = tok.get("refresh_token")
    if new_rtok and new_rtok != rtok:
        kc_set("refresh_token", new_rtok)  # Intuit rotates refresh tokens; keep the latest
    days = int(tok.get("x_refresh_token_expires_in", 0)) // 86400
    if days and days < 14:
        log("Warning: refresh token expires in %d days. Re-run setup soon." % days)


def access_token():
    if not _access_token or datetime.datetime.now() >= _access_expiry:
        refresh_access_token()
    return _access_token


def api_get(path, params=None):
    """GET only. All company data is read through here."""
    realm = kc_get("realm_id")
    if not realm:
        raise QboError("Realm (company) ID missing. Run: python3 qbo_mcp.py setup")
    params = dict(params or {})
    params["minorversion"] = MINOR_VERSION
    url = "%s/v3/company/%s/%s?%s" % (API_BASE, realm, path, urllib.parse.urlencode(params))
    for attempt in (1, 2):
        req = urllib.request.Request(url, method="GET", headers={
            "Authorization": "Bearer " + access_token(),
            "Accept": "application/json",
        })
        assert req.get_method() == "GET"
        try:
            return _http(req)
        except QboError as e:
            if attempt == 1 and "HTTP 401" in str(e):
                refresh_access_token()
                continue
            raise


# ---------------------------------------------------------------- Reports

def _cells(coldata):
    return [c.get("value", "") for c in (coldata or [])]


def flatten_report(rep):
    """Turns the nested QBO report JSON into a header plus flat rows.
    Each row: [level, kind, *cells] where kind is data, header or total."""
    cols = [c.get("ColTitle", "") or c.get("ColType", "") for c in rep.get("Columns", {}).get("Column", [])]
    rows = []

    def walk(block, level):
        for r in (block or {}).get("Row", []):
            if r.get("type") == "Section" or "Rows" in r or "Header" in r:
                if r.get("Header"):
                    rows.append([level, "header"] + _cells(r["Header"].get("ColData")))
                walk(r.get("Rows"), level + 1)
                if r.get("Summary"):
                    rows.append([level, "total"] + _cells(r["Summary"].get("ColData")))
            else:
                rows.append([level, "data"] + _cells(r.get("ColData")))

    walk(rep.get("Rows"), 0)
    return cols, rows


def report_text(rep, max_rows=400):
    h = rep.get("Header", {})
    cols, rows = flatten_report(rep)
    head = [
        "%s: %s" % (h.get("ReportName", "Report"), h.get("ReportBasis", "")),
        "Period: %s to %s | Currency: %s | Run: %s" % (
            h.get("StartPeriod", ""), h.get("EndPeriod", ""), h.get("Currency", ""), h.get("Time", "")),
    ]
    for o in h.get("Option", []) or []:
        if o.get("Name") == "NoReportData" and o.get("Value") == "true":
            head.append("No data for this period.")
    lines = head + ["", " | ".join(cols)]
    for r in rows[:max_rows]:
        level, kind, cells = r[0], r[1], r[2:]
        label = ("  " * level) + (cells[0] if cells else "")
        if kind == "total":
            label = label.upper() if label.strip() else ("  " * level) + "TOTAL"
        lines.append(" | ".join([label] + cells[1:]))
    if len(rows) > max_rows:
        lines.append("... %d more rows not shown. Use save_csv to get the full report." % (len(rows) - max_rows))
    return "\n".join(lines)


def save_report_csv(rep, report_name, label=None):
    h = rep.get("Header", {})
    cols, rows = flatten_report(rep)
    today = datetime.date.today().isoformat()
    period = "%s_to_%s" % (h.get("StartPeriod", ""), h.get("EndPeriod", ""))
    desc = re.sub(r"[^A-Za-z0-9]+", "_", label or report_name).strip("_")
    period = re.sub(r"[^A-Za-z0-9_]", "", period)
    os.makedirs(EXPORT_DIR, exist_ok=True)
    n = 1
    while True:
        path = os.path.join(EXPORT_DIR, "%s_QBO_%s_%s_v%d.csv" % (today, desc, period, n))
        try:
            f = open(path, "x", newline="", encoding="utf-8-sig")
            break
        except FileExistsError:
            n += 1
    with f:
        w = csv.writer(f)
        w.writerow(["# " + h.get("ReportName", report_name), h.get("ReportBasis", ""),
                    h.get("StartPeriod", ""), h.get("EndPeriod", ""), "run " + h.get("Time", "")])
        w.writerow(["level", "row_type"] + cols)
        for r in rows:
            w.writerow(r)
    return path


def run_report(name, start_date=None, end_date=None, accounting_method=None,
               summarize_column_by=None, date_macro=None, extra=None):
    if name not in REPORTS:
        raise QboError("Unknown report %r. Allowed: %s" % (name, ", ".join(sorted(REPORTS))))
    p = {}
    if start_date:
        p["start_date"] = start_date
    if end_date:
        p["end_date"] = end_date
    if date_macro and not (start_date or end_date):
        p["date_macro"] = date_macro
    if accounting_method:
        p["accounting_method"] = accounting_method.capitalize()
    if summarize_column_by:
        p["summarize_column_by"] = summarize_column_by
    for k, v in (extra or {}).items():
        if not re.match(r"^[a-z_]+$", str(k)):
            raise QboError("Bad report parameter name %r" % k)
        p[k] = v
    return api_get("reports/" + name, p)


# ---------------------------------------------------------------- Query / get

_FORBIDDEN = re.compile(r"\b(insert|update|delete|create|drop|alter|merge|void)\b", re.I)


def run_query(sql, max_rows=1000):
    s = sql.strip().rstrip(";").strip()
    if not s.lower().startswith("select ") or ";" in s or _FORBIDDEN.search(s):
        raise QboError("Only a single SELECT statement is allowed.")
    if re.search(r"\b(startposition|maxresults)\b", s, re.I):
        return api_get("query", {"query": s}).get("QueryResponse", {})
    out, start, page = {}, 1, 1000
    while True:
        q = "%s STARTPOSITION %d MAXRESULTS %d" % (s, start, page)
        resp = api_get("query", {"query": q}).get("QueryResponse", {})
        got = 0
        for k, v in resp.items():
            if isinstance(v, list):
                out.setdefault(k, []).extend(v)
                got = max(got, len(v))
            elif k not in ("startPosition", "maxResults"):
                out[k] = v
        total = sum(len(v) for v in out.values() if isinstance(v, list))
        if got < page or total >= max_rows:
            break
        start += page
    for k, v in out.items():
        if isinstance(v, list) and len(v) > max_rows:
            out[k] = v[:max_rows]
            out["truncated_at"] = max_rows
    return out


def get_entity(entity, entity_id=None):
    if entity not in ENTITIES:
        raise QboError("Unknown entity %r. Allowed: %s" % (entity, ", ".join(sorted(ENTITIES))))
    if entity == "CompanyInfo":
        realm = kc_get("realm_id")
        if not realm:
            raise QboError("Realm (company) ID missing. Run: python3 qbo_mcp.py setup")
        return api_get("companyinfo/" + realm)
    if entity == "Preferences":
        return api_get("preferences")
    if not entity_id or not re.match(r"^[0-9]+$", str(entity_id)):
        raise QboError("entity_id must be the numeric QBO Id.")
    return api_get("%s/%s" % (entity.lower(), entity_id))


def company_summary():
    ci = get_entity("CompanyInfo").get("CompanyInfo", {})
    addr = ci.get("CompanyAddr", {}) or {}
    return "\n".join([
        "Company: %s" % ci.get("CompanyName"),
        "Legal name: %s" % ci.get("LegalName"),
        "Country: %s" % ci.get("Country"),
        "Address: %s" % ", ".join(x for x in [addr.get("Line1"), addr.get("City"),
                                               addr.get("CountrySubDivisionCode"), addr.get("PostalCode")] if x),
        "Financial year starts: %s" % ci.get("FiscalYearStartMonth"),
        "Company created in QBO: %s" % (ci.get("MetaData", {}) or {}).get("CreateTime"),
    ])


# ---------------------------------------------------------------- MCP

DATE = {"type": "string", "description": "YYYY-MM-DD"}
TOOLS = [
    {
        "name": "qbo_company_info",
        "description": "Read the connected QuickBooks Online company details. Use it to check the connection works.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "qbo_report",
        "description": (
            "Run a QuickBooks Online report for the connected company (read only) and return it as text, "
            "optionally also saving the full report as a CSV in the export folder (QBO_EXPORT_DIR). "
            "Reports: " + "; ".join("%s (%s)" % (k, v) for k, v in REPORTS.items()) + ". "
            "Amounts are in AUD as recorded in QBO and exclude GST unless the report says otherwise."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "report": {"type": "string", "enum": sorted(REPORTS)},
                "start_date": DATE,
                "end_date": DATE,
                "date_macro": {"type": "string", "description": "Used only when no dates are given, e.g. 'This Fiscal Year-to-date', 'Last Month'."},
                "accounting_method": {"type": "string", "enum": ["Accrual", "Cash"]},
                "summarize_column_by": {"type": "string", "description": "Column split, e.g. Month, Quarter, Year, Customers, Classes, Total."},
                "params": {"type": "object", "description": "Extra QBO report parameters, e.g. {\"customer\": \"123\"} or {\"columns\": \"tx_date,txn_type,doc_num,name,memo,subt_nat_amount\"}."},
                "save_csv": {"type": "boolean", "description": "Also save the full report as a CSV in the export folder. Default false."},
                "max_rows": {"type": "integer", "description": "Rows of text to return. Default 400."},
            },
            "required": ["report"],
        },
    },
    {
        "name": "qbo_query",
        "description": (
            "Run a read-only QuickBooks query (SELECT only), e.g. \"SELECT * FROM Invoice WHERE TxnDate >= '2026-09-01'\" "
            "or \"SELECT Id, DisplayName, Balance FROM Customer WHERE Active = true\". Pages automatically up to max_rows."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string"},
                "max_rows": {"type": "integer", "description": "Default 1000."},
            },
            "required": ["sql"],
        },
    },
    {
        "name": "qbo_get",
        "description": "Read one QuickBooks record by entity type and numeric Id (read only). Entities: " + ", ".join(sorted(ENTITIES)) + ".",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity": {"type": "string", "enum": sorted(ENTITIES)},
                "entity_id": {"type": "string"},
            },
            "required": ["entity"],
        },
    },
]


def call_tool(name, args):
    if name == "qbo_company_info":
        return company_summary()
    if name == "qbo_report":
        rep = run_report(args["report"], args.get("start_date"), args.get("end_date"),
                         args.get("accounting_method"), args.get("summarize_column_by"),
                         args.get("date_macro"), args.get("params"))
        text = report_text(rep, int(args.get("max_rows") or 400))
        if args.get("save_csv"):
            text = "Saved full report to: %s\n\n%s" % (save_report_csv(rep, args["report"]), text)
        return text
    if name == "qbo_query":
        return json.dumps(run_query(args["sql"], int(args.get("max_rows") or 1000)), indent=1)
    if name == "qbo_get":
        return json.dumps(get_entity(args["entity"], args.get("entity_id")), indent=1)
    raise QboError("Unknown tool %s" % name)


def handle(msg):
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:
        return None  # notification
    if method == "initialize":
        pv = (msg.get("params") or {}).get("protocolVersion") or "2025-06-18"
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": pv,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": VERSION},
            "instructions": "Read-only access to the connected QuickBooks Online company. Nothing here can change QuickBooks.",
        }}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        p = msg.get("params") or {}
        try:
            text, err = call_tool(p.get("name"), p.get("arguments") or {}), False
        except (QboError, KeyError, ValueError) as e:
            text, err = "Error: %s" % e, True
        except Exception as e:  # keep the server alive
            text, err = "Unexpected error: %r" % e, True
        return {"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": text}], "isError": err}}
    if method in ("resources/list", "prompts/list"):
        key = method.split("/")[0]
        return {"jsonrpc": "2.0", "id": mid, "result": {key: []}}
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "Method not found: %s" % method}}


def serve():
    log("started, version %s" % VERSION)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": None,
                                         "error": {"code": -32700, "message": "Parse error"}}) + "\n")
            sys.stdout.flush()
            continue
        batch = msg if isinstance(msg, list) else [msg]
        replies = [r for r in (handle(m) for m in batch) if r is not None]
        if replies:
            sys.stdout.write(json.dumps(replies if isinstance(msg, list) else replies[0]) + "\n")
            sys.stdout.flush()


# ---------------------------------------------------------------- CLI

def setup():
    import getpass
    print("Stores the QuickBooks production credentials in the %s store (service '%s')." % (_backend(), KEYCHAIN_SERVICE))
    print("Press Enter to keep a value that is already stored.\n")
    for acct, prompt, secret in [
        ("client_id", "Production Client ID", False),
        ("client_secret", "Production Client Secret", True),
        ("realm_id", "Realm ID (Company ID)", False),
        ("refresh_token", "Refresh token from the OAuth Playground", True),
    ]:
        have = kc_get(acct)
        v = (getpass.getpass if secret else input)("%s%s: " % (prompt, " [stored]" if have else "")).strip()
        if v:
            kc_set(acct, v)
        elif not have:
            sys.exit("%s is required." % prompt)
    print("\nStored. Testing the connection...")
    print(company_summary())


def main(argv):
    if not argv:
        return serve()
    cmd = argv[0]
    try:
        if cmd == "setup":
            return setup()
        if cmd == "test":
            print(company_summary())
            return
        if cmd == "report":
            name = argv[1]
            dates = [a for a in argv[2:] if re.match(r"^\d{4}-\d{2}-\d{2}$", a)]
            extra = dict(a.split("=", 1) for a in argv[2:] if "=" in a)
            method = extra.pop("accounting_method", None)
            col = extra.pop("summarize_column_by", None)
            rep = run_report(name, dates[0] if dates else None, dates[1] if len(dates) > 1 else None,
                             method, col, None, extra)
            print(report_text(rep, 100000))
            print("\nSaved: " + save_report_csv(rep, name))
            return
        if cmd == "query":
            print(json.dumps(run_query(argv[1]), indent=1))
            return
        if cmd == "tools":
            print(json.dumps(TOOLS, indent=1))
            return
    except QboError as e:
        sys.exit("Error: %s" % e)
    sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
