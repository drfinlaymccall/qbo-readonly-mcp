# Connect QuickBooks Online

1. Create a QuickBooks Online app in the [Intuit developer portal](https://developer.intuit.com/).
2. For a trial, select development credentials and a sandbox company. For your business, use production credentials for your own app.
3. Follow [Intuit's OAuth procedure](https://developer.intuit.com/app/developer/qbo/docs/develop/authentication-and-authorization/oauth-2.0). Request the accounting scope, `com.intuit.quickbooks.accounting`.
4. In the Intuit OAuth Playground, authorise the intended company. Exchange the authorisation code for tokens.
5. For production, run `python3 qbo_mcp.py setup` from the checkout. Enter the client ID, client secret, realm ID and refresh token at the prompts.
6. For a sandbox on macOS or Linux, set both variables below before running setup. Use those same variables in the MCP client's server environment. On Windows, set them in the process environment before setup and in the client configuration.

```sh
export QBO_API_BASE=https://sandbox-quickbooks.api.intuit.com
export QBO_KEYCHAIN_SERVICE=qbo-readonly-sandbox
```

Setup reads the company details. Check the name before you use reports. The server stores credentials in macOS Keychain, Windows Credential Manager or Linux Secret Service. Linux needs the `secret-tool` command and an unlocked keyring.

The default export folder is `~/Documents/QBO Exports`. Set `QBO_EXPORT_DIR` to select another local folder. Disconnect the app in QuickBooks to revoke access.
