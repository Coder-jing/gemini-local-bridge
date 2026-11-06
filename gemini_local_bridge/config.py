"""Configuration model for gemini-local-bridge.

The config controls three safety-critical things:
  1. Which directories on disk the AI is allowed to touch (``allowed_roots``).
  2. How shell commands are policed (``command_policy``).
  3. Which transport Gemini uses to reach the server (``transport``).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

# Commands that are blocked by default in ``denylist`` mode. These are
# intentionally conservative: the goal is to stop obviously destructive or
# system-breaking operations while still allowing ordinary development work.
DEFAULT_DENY_COMMANDS: list[str] = [
    "rm",
    "rmdir",
    "del",
    "erase",
    "rd",
    "format",
    "mkfs",
    "diskpart",
    "shutdown",
    "reboot",
    "halt",
    "poweroff",
    "taskkill",
    "kill",
    "killall",
    "reg",
    "regedit",
    "bcdedit",
    "vssadmin",
    "cipher",
    "takeown",
    "icacls",
    "attrib",
    "net",
    "netsh",
    "sc",
    "schtasks",
    "wmic",
    "dd",
    "chmod",
    "chown",
    "mv",  # prefer fs_move tool so moves stay inside the sandbox
]

# Regex fragments that are blocked regardless of the base command. These catch
# destructive redirection / recursion that a bare command-name denylist misses.
DEFAULT_BLOCK_PATTERNS: list[str] = [
    r"rm\s+-[a-z]*r[a-z]*f",  # rm -rf and friends
    r"\bdel\s+/[sf]",  # del /s /f
    r"\brd\s+/s",
    r">\s*/dev/sd[a-z]",  # writing straight to a raw device
    r"\bmkfs\b",
    r"\b:\(\)\s*\{.*\};\s*:",  # fork bomb
    r"\b(shutdown|reboot|halt|poweroff)\b",
    r"\b(schtasks|at)\s+/create\b",
    r"(?i)\bformat\s+[a-z]:",
    r"\breg\s+(add|delete|import)\b",
]


class CommandPolicy(BaseModel):
    """How shell commands issued by the AI are policed."""

    mode: Literal["allowlist", "denylist", "unrestricted"] = "denylist"

    # For ``allowlist`` mode: the only base commands that may run.
    allow: list[str] = Field(default_factory=list)
    # For ``denylist`` mode: base commands that are always refused.
    deny: list[str] = Field(default_factory=lambda: list(DEFAULT_DENY_COMMANDS))
    # Regexes refused no matter what the base command is.
    block_patterns: list[str] = Field(
        default_factory=lambda: list(DEFAULT_BLOCK_PATTERNS)
    )
    # Per-command timeout in seconds.
    timeout_seconds: int = 60
    # Hard cap on captured stdout+stderr, to protect the context window.
    max_output_bytes: int = 200_000


class Config(BaseModel):
    """Top-level configuration."""

    server_name: str = "gemini-local-bridge"
    instructions: str = (
        "This server bridges Gemini to the local machine. Filesystem tools are "
        "confined to the configured allowed roots. Shell commands are policed by "
        "the configured command policy. Prefer the structured filesystem tools "
        "over shell commands for file work."
    )

    # Directories the AI may read/write. Relative paths are resolved against
    # the working directory at load time.
    allowed_roots: list[str] = Field(
        default_factory=lambda: [os.getcwd()]
    )

    transport: Literal["stdio", "streamable-http", "sse"] = "stdio"
    host: str = "127.0.0.1"
    port: int = 8765

    # --- Remote access auth ------------------------------------------------ #
    # Shared bearer token required on every HTTP/SSE request. Empty string or
    # null disables auth (only safe when bound to localhost behind a firewall).
    # Can also be supplied via the GEMINI_BRIDGE_TOKEN env var.
    auth_token: str = ""
    # When false, the token is accepted but not enforced (testing only).
    auth_enabled: bool = True

    # --- DNS-rebinding protection (public hostname) ------------------------ #
    # MCP's streamable-http transport validates the Host header. When the public
    # hostname (e.g. mcp.your-domain.com via Cloudflare) differs from localhost,
    # it must be allow-listed here or every request is rejected with 421.
    # cloudflared forwards the original Host header, so include the public domain.
    allowed_hosts: list[str] = Field(default_factory=list)
    allowed_origins: list[str] = Field(default_factory=list)

    command_policy: CommandPolicy = Field(default_factory=CommandPolicy)

    # Set to a path to record every tool call as JSON Lines. ``null``/"" disables.
    audit_log_path: str | None = "gemini_bridge_audit.jsonl"

    # Filesystem limits.
    max_read_bytes: int = 2_000_000
    max_write_bytes: int = 5_000_000
    max_entries: int = 2000

    # Master switches, so a deployment can expose only filesystem access.
    enable_filesystem: bool = True
    enable_shell: bool = True
    enable_system: bool = True
    # Extended tools: git, web fetch, image/pdf inspection.
    enable_git: bool = True
    enable_web: bool = True
    enable_content: bool = True

    def resolved_roots(self) -> list[Path]:
        """Return allowed roots as absolute, resolved Paths."""
        roots: list[Path] = []
        for raw in self.allowed_roots:
            p = Path(raw).expanduser()
            if not p.is_absolute():
                p = Path(os.getcwd()) / p
            roots.append(p.resolve())
        return roots

    @classmethod
    def load(cls, path: str | os.PathLike[str] | None = None) -> "Config":
        """Load config from a JSON file, falling back to defaults.

        Precedence: explicit ``path`` -> ``GEMINI_BRIDGE_CONFIG`` env var ->
        ``./gemini_bridge.config.json`` -> built-in defaults.
        """
        candidates: list[Path] = []
        if path is not None:
            candidates.append(Path(path))
        env = os.environ.get("GEMINI_BRIDGE_CONFIG")
        if env:
            candidates.append(Path(env))
        candidates.append(Path("gemini_bridge.config.json"))

        for cand in candidates:
            if cand and cand.is_file():
                import json

                data = json.loads(cand.read_text(encoding="utf-8"))
                return cls.model_validate(data)
        return cls()


def write_default_config(path: str | os.PathLike[str]) -> Path:
    """Write a commented-free default config JSON and return its path."""
    target = Path(path)
    cfg = Config()
    target.write_text(
        cfg.model_dump_json(indent=2), encoding="utf-8"
    )
    return target