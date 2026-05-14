from __future__ import annotations

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("echo-fixture")


@mcp.tool()
def echo(message: str) -> str:
    """Echo a message."""
    return message


@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


if __name__ == "__main__":
    mcp.run()
