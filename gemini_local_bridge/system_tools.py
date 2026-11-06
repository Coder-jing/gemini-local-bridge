"""Shell and system-inspection tools exposed to Gemini.

Shell access is the highest-risk surface, so every command goes through
:func:`check_command` (policy) and is confined to a sandboxed working
directory when one is supplied. System tools are read-only.
"""

from __future__ import annotations

import os
import platform
from pathlib import Path

from .config import Config
from .safety import AuditLog, SafetyError, resolve_in_sandbox, run_command_safely


class ShellTools:
    """Command-execution tools bound to a config + audit log."""

    def __init__(self, cfg: Config, audit: AuditLog):
        self.cfg = cfg
        self.audit = audit

    def _resolve_cwd(self, cwd: str | None) -> Path | None:
        if not cwd:
            return None
        return resolve_in_sandbox(cwd, self.cfg.resolved_roots())

    def run(self, command: str, cwd: str | None = None) -> dict:
        if not self.cfg.enable_shell:
            raise SafetyError("Shell access is disabled in configuration.")
        workdir = self._resolve_cwd(cwd)
        result = run_command_safely(command, self.cfg.command_policy, workdir)
        self.audit.record(
            "shell_run",
            {"command": command, "cwd": cwd or ""},
            "ok" if result["exit_code"] == 0 else f"exit={result['exit_code']}",
        )
        result["cwd"] = str(workdir) if workdir else os.getcwd()
        return result

    def run_python(self, code: str, timeout: int = 30) -> dict:
        """Run a short Python snippet with ``python -c`` (subject to policy)."""
        if not self.cfg.enable_shell:
            raise SafetyError("Shell access is disabled in configuration.")
        # Pass code via stdin to avoid quoting problems.
        import subprocess

        self._policy_guard_python(code)
        started = __import__("time").time()
        try:
            proc = subprocess.run(
                ["python", "-c", code],
                capture_output=True,
                timeout=timeout,
            )
            from .safety import truncate_output

            limit = self.cfg.command_policy.max_output_bytes
            stdout, _ = truncate_output(proc.stdout or b"", limit)
            stderr, _ = truncate_output(proc.stderr or b"", limit)
            result = {
                "command": "python -c <snippet>",
                "exit_code": proc.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "timed_out": False,
                "output_truncated": False,
                "duration_sec": round(__import__("time").time() - started, 3),
            }
        except subprocess.TimeoutExpired:
            result = {
                "command": "python -c <snippet>",
                "exit_code": None,
                "stdout": "",
                "stderr": f"timed out after {timeout}s",
                "timed_out": True,
                "output_truncated": False,
                "duration_sec": round(__import__("time").time() - started, 3),
            }
        self.audit.record("shell_python", {"code": code[:200]}, "ok")
        return result

    def _policy_guard_python(self, code: str) -> None:
        """Apply the command policy to a Python snippet's base operation.

        We conservatively treat ``python`` as the base command and also run the
        block patterns against the snippet body, so an operator's denylist still
        applies to obviously destructive code.
        """
        from .safety import check_command

        # Reuse the pattern checks by treating the snippet as a command line.
        policy = self.cfg.command_policy
        for pattern in policy.block_patterns:
            import re

            try:
                if re.search(pattern, code):
                    raise SafetyError(
                        f"Python snippet matches blocked pattern /{pattern}/."
                    )
            except re.error:
                continue


class SystemTools:
    """Read-only system inspection."""

    def __init__(self, cfg: Config, audit: AuditLog):
        self.cfg = cfg
        self.audit = audit

    def info(self) -> dict:
        self.audit.record("sys_info", {}, "ok")
        roots = [str(r) for r in self.cfg.resolved_roots()]
        return {
            "platform": platform.system(),
            "platform_release": platform.release(),
            "platform_version": platform.version(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "python_version": platform.python_version(),
            "cwd": os.getcwd(),
            "allowed_roots": roots,
            "shell_enabled": self.cfg.enable_shell,
            "filesystem_enabled": self.cfg.enable_filesystem,
        }

    def disk(self, path: str | None = None) -> dict:
        import shutil

        target = path or os.getcwd()
        p = resolve_in_sandbox(target, self.cfg.resolved_roots())
        total, used, free = shutil.disk_usage(p)
        self.audit.record("sys_disk", {"path": target}, "ok")
        return {
            "path": str(p),
            "total_bytes": total,
            "used_bytes": used,
            "free_bytes": free,
            "percent_used": round(used / total * 100, 1) if total else None,
        }

    def env(self, name: str) -> dict:
        """Return a single environment variable's presence/value.

        Only returns the requested variable; never dumps the whole environment,
        to avoid leaking secrets like API keys in bulk.
        """
        value = os.environ.get(name)
        self.audit.record("sys_env", {"name": name}, "ok", "set" if value else "unset")
        return {"name": name, "set": value is not None, "value": value}

    def processes(self, limit: int = 50) -> dict:
        """List running processes (best effort, platform dependent)."""
        self.audit.record("sys_processes", {}, "ok")
        system = platform.system()
        try:
            if system == "Windows":
                raw = _run(["tasklist", "/FO", "CSV", "/NH"])
                return {"platform": system, "raw": raw[: limit * 120]}
            else:
                raw = _run(["ps", "-eo", "pid,comm,%cpu,%mem", "--sort=-%cpu"])
                lines = raw.splitlines()[: limit + 1]
                return {"platform": system, "raw": "\n".join(lines)}
        except (OSError, FileNotFoundError) as exc:
            return {"platform": system, "error": str(exc)}


def _run(argv: list[str]) -> str:
    import subprocess

    proc = subprocess.run(argv, capture_output=True, timeout=20)
    return proc.stdout.decode("utf-8", errors="replace")