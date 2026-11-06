"""Safety layer: path sandboxing, command policy, and audit logging.

Everything the AI does through this server passes through one of these guards:

* :func:`resolve_in_sandbox` — ensures a filesystem path stays inside an
  allowed root. This is the single most important control: it is what stops
  the model from reading ``~/.ssh`` or overwriting system files.
* :class:`CommandPolicy` checks — decide whether a shell command may run.
* :class:`AuditLog` — appends a JSON-Lines record for every tool call so the
  human can see exactly what the model did.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
from pathlib import Path
from typing import Iterable

from .config import CommandPolicy, Config


class SafetyError(Exception):
    """Raised when an operation is refused by the safety layer."""


# --------------------------------------------------------------------------- #
# Path sandboxing
# --------------------------------------------------------------------------- #
def resolve_in_sandbox(path: str | os.PathLike[str], roots: Iterable[Path]) -> Path:
    """Resolve *path* and require it to live under one of *roots*.

    Symlinks are resolved, so a symlink that points outside a root is treated
    as outside. Raises :class:`SafetyError` on any escape attempt.
    """
    roots = [Path(r).resolve() for r in roots]
    if not roots:
        raise SafetyError("No allowed roots configured.")

    raw = Path(path).expanduser()
    if not raw.is_absolute():
        # Relative paths are interpreted against the first root, which makes
        # the common "work inside the project" case ergonomic.
        raw = roots[0] / raw
    resolved = raw.resolve()

    for root in roots:
        if resolved == root or root in resolved.parents:
            return resolved

    raise SafetyError(
        f"Path {resolved} is outside the allowed roots. "
        f"Allowed: {[str(r) for r in roots]}"
    )


def is_within(path: Path, root: Path) -> bool:
    path = path.resolve()
    root = root.resolve()
    return path == root or root in path.parents


# --------------------------------------------------------------------------- #
# Command policy
# --------------------------------------------------------------------------- #
def _base_command(command: str) -> str:
    """Best-effort extraction of the base command name.

    Handles simple quoting via shlex but falls back to a whitespace split,
    since Windows commands are not always POSIX-quoted.
    """
    try:
        parts = shlex.split(command, posix=False)
    except ValueError:
        parts = command.split()
    if not parts:
        return ""
    return Path(parts[0]).stem.lower()


def _iter_simple_commands(command: str) -> list[str]:
    """Split a composite command on shell separators for per-part checks."""
    # Split on &&, ||, ;, and | but keep it simple; this is a safety net,
    # not a full shell parser.
    fragments = re.split(r"&&|\|\||;|\|", command)
    return [f.strip() for f in fragments if f.strip()]


def check_command(command: str, policy: CommandPolicy) -> None:
    """Raise :class:`SafetyError` if *command* violates *policy*.

    In ``denylist`` mode every simple command's base name must not be denied.
    In ``allowlist`` mode every simple command's base name must be allowed.
    ``block_patterns`` are checked against the whole command line in both modes.
    """
    allow = {a.lower() for a in policy.allow}
    deny = {d.lower() for d in policy.deny}

    for frag in _iter_simple_commands(command):
        base = _base_command(frag)

        if policy.mode == "allowlist":
            if base not in allow:
                raise SafetyError(
                    f"Command '{base}' is not on the allowlist "
                    f"(allowed: {sorted(allow)})."
                )
        elif policy.mode == "denylist":
            if base in deny:
                raise SafetyError(f"Command '{base}' is denied by policy.")

    # Pattern checks run against the full command line.
    for pattern in policy.block_patterns:
        try:
            if re.search(pattern, command):
                raise SafetyError(
                    f"Command matches blocked pattern /{pattern}/."
                )
        except re.error:
            continue  # never let a bad operator regex crash the server


def truncate_output(data: bytes, limit: int) -> tuple[str, bool]:
    """Decode bytes to text, truncating and flagging if over *limit*."""
    truncated = len(data) > limit
    if truncated:
        data = data[:limit]
    text = data.decode("utf-8", errors="replace")
    if truncated:
        text += f"\n... [output truncated at {limit} bytes]"
    return text, truncated


def run_command_safely(
    command: str,
    policy: CommandPolicy,
    cwd: Path | None = None,
) -> dict:
    """Validate and execute *command*, returning a structured result."""
    check_command(command, policy)
    timeout = policy.timeout_seconds
    started = time.time()
    try:
        proc = subprocess.run(
            command,
            shell=True,  # the model writes shell syntax; run through the shell
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            timeout=timeout,
        )
        stdout, stdout_cut = truncate_output(proc.stdout or b"", policy.max_output_bytes)
        stderr, stderr_cut = truncate_output(proc.stderr or b"", policy.max_output_bytes)
        return {
            "command": command,
            "exit_code": proc.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "timed_out": False,
            "output_truncated": stdout_cut or stderr_cut,
            "duration_sec": round(time.time() - started, 3),
        }
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout or b""
        err = exc.stderr or b""
        stdout, _ = truncate_output(
            out if isinstance(out, bytes) else str(out).encode(),
            policy.max_output_bytes,
        )
        stderr, _ = truncate_output(
            err if isinstance(err, bytes) else str(err).encode(),
            policy.max_output_bytes,
        )
        return {
            "command": command,
            "exit_code": None,
            "stdout": stdout,
            "stderr": stderr,
            "timed_out": True,
            "output_truncated": False,
            "duration_sec": round(time.time() - started, 3),
        }


# --------------------------------------------------------------------------- #
# Audit log
# --------------------------------------------------------------------------- #
class AuditLog:
    """Append-only JSON-Lines audit trail."""

    def __init__(self, path: str | os.PathLike[str] | None):
        self.path = Path(path) if path else None
        if self.path:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
            except OSError:
                self.path = None

    def record(self, tool: str, args: dict, outcome: str, detail: str = "") -> None:
        if not self.path:
            return
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "tool": tool,
            "args": _safe_args(args),
            "outcome": outcome,
            "detail": detail,
        }
        try:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass  # auditing must never break the actual tool


def _safe_args(args: dict, max_len: int = 400) -> dict:
    """Shrink argument values so the audit log stays readable."""
    out: dict = {}
    for k, v in args.items():
        s = str(v)
        out[k] = s if len(s) <= max_len else s[:max_len] + "…"
    return out


def build_audit(cfg: Config) -> AuditLog:
    return AuditLog(cfg.audit_log_path)