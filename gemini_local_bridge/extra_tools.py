"""Extra tools exposed to Gemini: git, web fetch, and image/PDF inspection.

These extend the core filesystem/shell/system set with higher-level developer
and content tools. Git operations are confined to the sandbox (they run with a
sandboxed working directory). Web fetch is outbound-only and returns text.
"""

from __future__ import annotations

import base64
import subprocess
from pathlib import Path

from .config import Config
from .safety import AuditLog, SafetyError, resolve_in_sandbox


def _run_git(argv: list[str], cwd: Path, timeout: int = 30) -> dict:
    """Run a git subcommand in *cwd*, returning a structured result."""
    try:
        proc = subprocess.run(
            ["git", *argv],
            cwd=str(cwd),
            capture_output=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        raise SafetyError("git is not installed or not on PATH.")
    except subprocess.TimeoutExpired:
        raise SafetyError(f"git {argv[0]} timed out.")
    return {
        "exit_code": proc.returncode,
        "stdout": proc.stdout.decode("utf-8", errors="replace"),
        "stderr": proc.stderr.decode("utf-8", errors="replace"),
    }


class GitTools:
    """Git operations, confined to sandboxed repositories."""

    def __init__(self, cfg: Config, audit: AuditLog):
        self.cfg = cfg
        self.audit = audit

    def _repo(self, path: str) -> Path:
        p = resolve_in_sandbox(path, self.cfg.resolved_roots())
        if not p.is_dir():
            raise SafetyError(f"Not a directory: {p}")
        return p

    def _guard(self, path: str, argv: list[str], name: str, timeout: int = 30) -> dict:
        cwd = self._repo(path)
        result = _run_git(argv, cwd, timeout)
        self.audit.record(f"git_{name}", {"path": path}, "ok" if result["exit_code"] == 0 else "nonzero")
        result["path"] = str(cwd)
        return result

    def status(self, path: str) -> dict:
        return {"ok": True, **self._guard(path, ["status", "--short", "--branch"], "status")}

    def log(self, path: str, count: int = 20) -> dict:
        n = max(1, min(count, 200))
        return {"ok": True, **self._guard(path, ["log", f"-{n}", "--oneline", "--decorate"], "log")}

    def diff(self, path: str, staged: bool = False) -> dict:
        argv = ["diff", "--stat"] + (["--cached"] if staged else [])
        return {"ok": True, **self._guard(path, argv, "diff")}

    def diff_full(self, path: str, staged: bool = False) -> dict:
        argv = ["diff"] + (["--cached"] if staged else [])
        return {"ok": True, **self._guard(path, argv, "diff_full")}

    def branches(self, path: str) -> dict:
        return {"ok": True, **self._guard(path, ["branch", "-a"], "branches")}

    def add(self, path: str, files: list[str] | None = None) -> dict:
        argv = ["add", *(files or ["-A"])]
        return {"ok": True, **self._guard(path, argv, "add")}

    def commit(self, path: str, message: str) -> dict:
        if not message.strip():
            raise SafetyError("Commit message must not be empty.")
        return {"ok": True, **self._guard(path, ["commit", "-m", message], "commit")}

    def push(self, path: str, remote: str = "origin", branch: str | None = None) -> dict:
        argv = ["push", remote] + ([branch] if branch else [])
        return {"ok": True, **self._guard(path, argv, "push", timeout=120)}

    def pull(self, path: str, remote: str = "origin", branch: str | None = None) -> dict:
        argv = ["pull", remote] + ([branch] if branch else [])
        return {"ok": True, **self._guard(path, argv, "pull", timeout=120)}


class WebTools:
    """Outbound web fetch, returning text content."""

    def __init__(self, cfg: Config, audit: AuditLog):
        self.cfg = cfg
        self.audit = audit

    def fetch(self, url: str, max_bytes: int = 200_000) -> dict:
        if not url.startswith(("http://", "https://")):
            raise SafetyError("URL must start with http:// or https://")
        import httpx

        try:
            with httpx.Client(timeout=20, follow_redirects=True) as client:
                resp = client.get(url)
        except httpx.HTTPError as exc:
            raise SafetyError(f"Fetch failed: {exc}")

        content_type = resp.headers.get("content-type", "")
        text = resp.text[:max_bytes]
        truncated = len(resp.text) > max_bytes
        self.audit.record("web_fetch", {"url": url}, "ok", f"status={resp.status_code}")
        return {
            "ok": True,
            "url": str(resp.url),
            "status_code": resp.status_code,
            "content_type": content_type,
            "truncated": truncated,
            "content": text,
        }


class ContentTools:
    """Image / PDF inspection (metadata + text extraction)."""

    def __init__(self, cfg: Config, audit: AuditLog):
        self.cfg = cfg
        self.audit = audit

    def _resolve(self, path: str) -> Path:
        p = resolve_in_sandbox(path, self.cfg.resolved_roots())
        if not p.is_file():
            raise SafetyError(f"Not a file: {p}")
        return p

    def image_info(self, path: str) -> dict:
        """Return image dimensions, format, and a base64 thumbnail is NOT included."""
        p = self._resolve(path)
        try:
            from PIL import Image

            with Image.open(p) as im:
                info = {
                    "format": im.format,
                    "mode": im.mode,
                    "width": im.width,
                    "height": im.height,
                    "size_bytes": p.stat().st_size,
                }
        except ImportError:
            raise SafetyError("Pillow is not installed (pip install pillow).")
        except Exception as exc:  # noqa: BLE001
            raise SafetyError(f"Could not read image: {exc}")
        self.audit.record("image_info", {"path": path}, "ok")
        return {"ok": True, "path": str(p), **info}

    def pdf_text(self, path: str, max_pages: int = 20) -> dict:
        """Extract text from a PDF (first *max_pages* pages)."""
        p = self._resolve(path)
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(p))
            pages = min(max_pages, len(reader.pages))
            text = "\n".join(
                (page.extract_text() or "") for page in reader.pages[:pages]
            )
        except ImportError:
            raise SafetyError("pypdf is not installed (pip install pypdf).")
        except Exception as exc:  # noqa: BLE001
            raise SafetyError(f"Could not read PDF: {exc}")
        self.audit.record("pdf_text", {"path": path}, "ok", f"{pages} pages")
        return {
            "ok": True,
            "path": str(p),
            "total_pages": len(reader.pages),
            "extracted_pages": pages,
            "text": text[: self.cfg.max_read_bytes],
        }