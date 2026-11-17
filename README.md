# QuickBooks Online for Claude and Codex

Read company details, financial reports and accounting records through a private Intuit app. This connector is part of the Business Operating System series.

The server exposes four MCP tools. Company data requests use HTTP GET. The query tool accepts a single SELECT statement. OAuth renewal sends a POST to Intuit. The accounting token has broader permissions than these tools. Report export writes a CSV to the local computer. Tool results enter the selected assistant's context.

## Connect a client

Use Python 3.8 or later. Keep this checkout in a stable location. Complete the [Intuit setup](HANDOVER.md) before requesting company data.

| Client | Registration |
| --- | --- |
| Claude Code | From the project where you want access, run `python3 /absolute/path/to/register_client.py claude`. Registration uses local project scope. |
| Codex | Run `python3 /absolute/path/to/register_client.py codex`. Registration uses the Codex user configuration. |
| Claude Desktop on macOS | From the checkout, run `bash install.sh`. Restart Claude Desktop. |
| Claude Desktop on Windows | Run `.\install.ps1` from PowerShell. Restart Claude Desktop. |

On Windows, use the Python launcher `py -3` in place of `python3`. Start a new client session. Ask it to show the connected company name. Use one active client per credential set to avoid simultaneous token refresh.

## Verify without an account

Run `python3 test_qbo.py` and `python3 test_backends.py`. The tests use synthetic records and simulated credential stores. Windows installation and live Australian company access still require verification.

## Related implementations

[Intuit's server](https://github.com/intuit/quickbooks-online-mcp-server) supports accounting changes and provides switches for disabling tool categories. [Alvey Automation](https://github.com/alveyautomation/qbo-mcp) documents eight read-only endpoints. [Nichewizard](https://github.com/nichewizard/quickbooks-mcp) provides separate read and write launchers. Both clients use the same local Python server.
