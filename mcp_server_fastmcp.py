#!/usr/bin/env -S uv run --script
# /// script
# dependencies = [
#     "fastmcp[server]>=2.6.0",
# ]
# ///
""" 
MCP Server with Google OAuth using FastMCP.

Run:
    ./mcp_server_fastmcp.py
"""

import os
from datetime import datetime

from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.server.auth.providers.google import GoogleProvider

load_dotenv()

# =============================================================================
# Configuration
# =============================================================================

GOOGLE_CLIENT_ID = os.environ["GOOGLE_CLIENT_ID"]
GOOGLE_CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
BASE_URL = os.environ["BASE_URL"]
HTTP_PORT = int(os.environ["HTTP_PORT"])

# =============================================================================
# Auth Setup
# =============================================================================

auth = GoogleProvider(
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    base_url=BASE_URL,
)

# =============================================================================
# MCP Server
# =============================================================================

mcp = FastMCP(
    name="MCP Server with Google OAuth",
    auth=auth,
)


@mcp.tool()
def echo(message: str) -> str:
    """Echo back the input message."""
    return f"Echo: {message}"


@mcp.tool()
def get_time() -> str:
    """Get the current server time."""
    return f"Server time: {datetime.utcnow().isoformat()} UTC"


@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


# =============================================================================
# Main
# =============================================================================

if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=HTTP_PORT)
