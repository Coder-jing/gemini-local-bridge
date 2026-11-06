"""gemini-local-bridge: connect Gemini to local system resources over MCP."""

from .config import Config, CommandPolicy
from .server import create_server, main

__all__ = ["Config", "CommandPolicy", "create_server", "main"]
__version__ = "1.0.0"