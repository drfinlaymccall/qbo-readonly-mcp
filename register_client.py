"""Register the local QuickBooks MCP server with Claude Code or Codex."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("client", choices=["claude", "codex"])
    client = parser.parse_args().client
    executable = shutil.which(client)
    if not executable:
        parser.exit(1, f"Install the {client} CLI first.\n")
    server = Path(__file__).resolve().with_name("qbo_mcp.py")
    options = ["--scope", "local"] if client == "claude" else []
    return subprocess.call([executable, "mcp", "add", *options, "qbo-readonly",
                            "--", sys.executable, str(server)])


if __name__ == "__main__":
    sys.exit(main())
