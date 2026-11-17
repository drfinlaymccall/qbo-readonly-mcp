# QuickBooks Online for Claude and Codex

I published this connector in the Business Operating System series after we tried to connect Claude to an Australian QuickBooks Online company and Intuit's official connector would not connect. We wanted to ask Claude for a profit and loss report directly from the books.

Other Australian users reported the same problem at authorisation. On 13 October 2026, a QuickBooks community manager confirmed that Intuit offered its Claude connector only to US customers. Intuit said it was working on international availability and gave no Australian release date. [QuickBooks Community response](https://quickbooks.intuit.com/community/other-questions-9/australia-claude-anthropic-quickbooks-connector-blocked-app-isn-t-available-for-use-in-your-country-please-give-au-date-376577)

This connector takes a separate route. You authorise your own Intuit app. A small server on your computer then lets Claude or Codex read reports and records through the QuickBooks Online Accounting API. Claude Code connected in testing. A live read from an Australian company still needs verification.

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
