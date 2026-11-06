"""FastMCP server that exposes local resources to Gemini.

The server registers three families of tools (filesystem, shell, system) plus a
small set of resources for introspection. It can run over stdio (for Gemini
CLI) or over streamable-HTTP / SSE (for Gemini web and AI Studio, which reach a
running HTTP endpoint).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from .config import Config
from .extra_tools import ContentTools, GitTools, WebTools
from .fs_tools import FileSystemTools
from .safety import SafetyError, build_audit
from .system_tools import ShellTools, SystemTools


def _err(message: str) -> dict[str, Any]:
    return {"ok": False, "error": message}


def _build_transport_security(cfg: Config):
    """Build MCP TransportSecuritySettings from config.

    FastMCP enables DNS-rebinding protection by default when bound to a
    loopback address, allow-listing only 127.0.0.1/localhost. That breaks
    remote access through a tunnel (cloudflared forwards the public Host
    header), so we explicitly allow the configured public host(s) while
    keeping localhost working for local use.
    """
    from mcp.server.transport_security import TransportSecuritySettings

    hosts = list(cfg.allowed_hosts or [])
    # Always keep loopback working for local Gemini CLI / healthz.
    for local in ("127.0.0.1:*", "localhost:*", "[::1]:*"):
        if local not in hosts:
            hosts.append(local)

    origins = list(cfg.allowed_origins or [])

    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=origins,
    )


def create_server(cfg: Config | None = None) -> FastMCP:
    """Build and return a configured :class:`FastMCP` instance.

    Tool handlers wrap the underlying tool classes and translate
    :class:`SafetyError` into a structured ``{"ok": False, "error": ...}``
    result so the model can read the refusal instead of the call blowing up.
    """
    cfg = cfg or Config.load()
    audit = build_audit(cfg)
    fs = FileSystemTools(cfg, audit)
    shell = ShellTools(cfg, audit)
    sysc = SystemTools(cfg, audit)
    git = GitTools(cfg, audit)
    web = WebTools(cfg, audit)
    content = ContentTools(cfg, audit)

    # DNS-rebinding protection: when exposing a public hostname (e.g. via
    # Cloudflare Tunnel), cloudflared forwards the original Host header, which
    # is NOT localhost. Without allow-listing it, MCP rejects every request
    # with 421 "Invalid Host header". Build a TransportSecuritySettings that
    # permits the configured public host(s) plus localhost.
    transport_security = _build_transport_security(cfg)

    server = FastMCP(
        cfg.server_name,
        instructions=cfg.instructions,
        host=cfg.host,
        port=cfg.port,
        transport_security=transport_security,
    )

    # ------------------------------------------------------------------ #
    # Filesystem tools
    # ------------------------------------------------------------------ #
    if cfg.enable_filesystem:

        @server.tool()
        def fs_list(path: str = ".") -> dict:
            """List a directory's entries (name, type, size) inside the sandbox."""
            try:
                return {"ok": True, **fs.list_dir(path)}
            except SafetyError as e:
                return _err(str(e))

        @server.tool()
        def fs_read(path: str, max_bytes: int | None = None) -> dict:
            """Read a text file, returning its content (truncated to limits)."""
            try:
                return {"ok": True, **fs.read_file(path, max_bytes)}
            except SafetyError as e:
                return _err(str(e))

        @server.tool()
        def fs_write(path: str, content: str, overwrite: bool = True) -> dict:
            """Create or overwrite a text file with *content*."""
            try:
                return {"ok": True, **fs.write_file(path, content, overwrite)}
            except SafetyError as e:
                return _err(str(e))

        @server.tool()
        def fs_append(path: str, content: str) -> dict:
            """Append *content* to a file, creating it if missing."""
            try:
                return {"ok": True, **fs.append_file(path, content)}
            except SafetyError as e:
                return _err(str(e))

        @server.tool()
        def fs_edit(
            path: str, old_string: str, new_string: str, replace_all: bool = False
        ) -> dict:
            """Replace *old_string* with *new_string* in a file."""
            try:
                return {
                    "ok": True,
                    **fs.edit_file(path, old_string, new_string, replace_all),
                }
            except SafetyError as e:
                return _err(str(e))

        @server.tool()
        def fs_search(
            root: str,
            pattern: str,
            glob: str = "*",
            max_results: int = 100,
            include_hidden: bool = False,
        ) -> dict:
            """Search file contents under *root* for *pattern* (substring)."""
            try:
                return {
                    "ok": True,
                    **fs.search(root, pattern, glob, max_results, include_hidden),
                }
            except SafetyError as e:
                return _err(str(e))

        @server.tool()
        def fs_tree(path: str = ".", depth: int = 3) -> dict:
            """Return an ASCII tree of a directory up to *depth* levels."""
            try:
                return {"ok": True, **fs.tree(path, depth)}
            except SafetyError as e:
                return _err(str(e))

        @server.tool()
        def fs_info(path: str) -> dict:
            """Return metadata (type, size, mtime) for a path."""
            try:
                return {"ok": True, **fs.get_info(path)}
            except SafetyError as e:
                return _err(str(e))

        @server.tool()
        def fs_mkdir(path: str) -> dict:
            """Create a directory (and parents) inside the sandbox."""
            try:
                return {"ok": True, **fs.mkdir(path)}
            except SafetyError as e:
                return _err(str(e))

        @server.tool()
        def fs_move(src: str, dst: str) -> dict:
            """Move/rename a file or directory; both paths must be in the sandbox."""
            try:
                return {"ok": True, **fs.move(src, dst)}
            except SafetyError as e:
                return _err(str(e))

        @server.tool()
        def fs_copy(src: str, dst: str) -> dict:
            """Copy a file or directory; both paths must be in the sandbox."""
            try:
                return {"ok": True, **fs.copy(src, dst)}
            except SafetyError as e:
                return _err(str(e))

        @server.tool()
        def fs_delete(path: str, recursive: bool = False) -> dict:
            """Delete a file or directory (recursive required for non-empty dirs)."""
            try:
                return {"ok": True, **fs.delete(path, recursive)}
            except SafetyError as e:
                return _err(str(e))

    # ------------------------------------------------------------------ #
    # Shell tools
    # ------------------------------------------------------------------ #
    if cfg.enable_shell:

        @server.tool()
        def shell_run(command: str, cwd: str | None = None) -> dict:
            """Run a shell command subject to the command policy; returns exit/stdout/stderr."""
            try:
                result = shell.run(command, cwd)
                return {"ok": True, **result}
            except SafetyError as e:
                return _err(str(e))

        @server.tool()
        def shell_python(code: str, timeout: int = 30) -> dict:
            """Run a short Python snippet via ``python -c`` (policy-checked)."""
            try:
                return {"ok": True, **shell.run_python(code, timeout)}
            except SafetyError as e:
                return _err(str(e))

    # ------------------------------------------------------------------ #
    # System tools
    # ------------------------------------------------------------------ #
    if cfg.enable_system:

        @server.tool()
        def sys_info() -> dict:
            """Return OS, Python, and sandbox configuration summary."""
            return {"ok": True, **sysc.info()}

        @server.tool()
        def sys_disk(path: str | None = None) -> dict:
            """Return disk usage for a path inside the sandbox."""
            try:
                return {"ok": True, **sysc.disk(path)}
            except SafetyError as e:
                return _err(str(e))

        @server.tool()
        def sys_env(name: str) -> dict:
            """Return a single environment variable (never the whole env)."""
            return {"ok": True, **sysc.env(name)}

        @server.tool()
        def sys_processes(limit: int = 50) -> dict:
            """List running processes (best effort per platform)."""
            return {"ok": True, **sysc.processes(limit)}

    # ------------------------------------------------------------------ #
    # Git tools
    # ------------------------------------------------------------------ #
    if cfg.enable_git:

        @server.tool()
        def git_status(path: str) -> dict:
            """Show git working-tree status for a repo inside the sandbox."""
            try:
                return git.status(path)
            except SafetyError as e:
                return _err(str(e))

        @server.tool()
        def git_log(path: str, count: int = 20) -> dict:
            """Show recent commit history (oneline) for a repo."""
            try:
                return git.log(path, count)
            except SafetyError as e:
                return _err(str(e))

        @server.tool()
        def git_diff(path: str, staged: bool = False, full: bool = False) -> dict:
            """Show git diff (--stat, or full with full=true); staged via staged=true."""
            try:
                return git.diff_full(path, staged) if full else git.diff(path, staged)
            except SafetyError as e:
                return _err(str(e))

        @server.tool()
        def git_branches(path: str) -> dict:
            """List local and remote branches."""
            try:
                return git.branches(path)
            except SafetyError as e:
                return _err(str(e))

        @server.tool()
        def git_add(path: str, files: list[str] | None = None) -> dict:
            """Stage files (default: all changes) for commit."""
            try:
                return git.add(path, files)
            except SafetyError as e:
                return _err(str(e))

        @server.tool()
        def git_commit(path: str, message: str) -> dict:
            """Commit staged changes with a message."""
            try:
                return git.commit(path, message)
            except SafetyError as e:
                return _err(str(e))

        @server.tool()
        def git_push(path: str, remote: str = "origin", branch: str | None = None) -> dict:
            """Push commits to a remote (default origin)."""
            try:
                return git.push(path, remote, branch)
            except SafetyError as e:
                return _err(str(e))

        @server.tool()
        def git_pull(path: str, remote: str = "origin", branch: str | None = None) -> dict:
            """Pull changes from a remote (default origin)."""
            try:
                return git.pull(path, remote, branch)
            except SafetyError as e:
                return _err(str(e))

    # ------------------------------------------------------------------ #
    # Web tools
    # ------------------------------------------------------------------ #
    if cfg.enable_web:

        @server.tool()
        def web_fetch(url: str, max_bytes: int = 200_000) -> dict:
            """Fetch a URL over HTTP(S) and return its text content."""
            try:
                return web.fetch(url, max_bytes)
            except SafetyError as e:
                return _err(str(e))

    # ------------------------------------------------------------------ #
    # Content tools (image / PDF)
    # ------------------------------------------------------------------ #
    if cfg.enable_content:

        @server.tool()
        def image_info(path: str) -> dict:
            """Return image metadata (format, mode, width, height, size)."""
            try:
                return content.image_info(path)
            except SafetyError as e:
                return _err(str(e))

        @server.tool()
        def pdf_text(path: str, max_pages: int = 20) -> dict:
            """Extract text from a PDF (first max_pages pages)."""
            try:
                return content.pdf_text(path, max_pages)
            except SafetyError as e:
                return _err(str(e))

    # ------------------------------------------------------------------ #
    # Resources: let the client read the active policy and sandbox roots.
    # ------------------------------------------------------------------ #
    @server.resource("config://sandbox")
    def sandbox_resource() -> str:
        """Current allowed roots and shell policy, as JSON."""
        policy = cfg.command_policy
        return json.dumps(
            {
                "allowed_roots": [str(r) for r in cfg.resolved_roots()],
                "shell_enabled": cfg.enable_shell,
                "filesystem_enabled": cfg.enable_filesystem,
                "command_policy": {
                    "mode": policy.mode,
                    "allow": policy.allow,
                    "deny": policy.deny,
                    "timeout_seconds": policy.timeout_seconds,
                },
            },
            indent=2,
        )

    return server


def main() -> None:
    """Console-script / ``python -m`` entry point."""
    cfg = Config.load()
    server = create_server(cfg)
    transport = cfg.transport
    if transport == "stdio":
        server.run("stdio")
    elif transport == "sse":
        server.run("sse")
    else:
        run_http_with_auth(server, cfg)


def run_http_with_auth(server: FastMCP, cfg: Config) -> None:
    """Run the streamable-HTTP app via uvicorn with optional token auth.

    FastMCP's own ``run()`` builds and hosts the app internally, which gives us
    no chance to install auth middleware. So for the *remote* transport we fetch
    the underlying Starlette app, install the middleware *on it directly* (this
    preserves FastMCP's lifespan, which boots the session-manager task group),
    and host it ourselves. stdio (local Gemini CLI) is unaffected and auth-free.

    A public ``/healthz`` route is added so Cloudflare / load balancers can probe
    the tunnel without a token.
    """
    import uvicorn

    from .auth import TokenAuthMiddleware, load_token

    @server.custom_route("/healthz", methods=["GET"])
    def _healthz(_request):  # pragma: no cover - trivial
        from starlette.responses import JSONResponse

        return JSONResponse({"status": "ok", "server": cfg.server_name})

    # IMPORTANT: streamable_http_app() builds a *fresh* Starlette app on every
    # call, so we must grab it ONCE, install middleware on that exact instance,
    # and hand that same instance to uvicorn. Calling it twice would silently
    # drop the auth middleware.
    app = server.streamable_http_app()

    token = load_token(cfg.auth_token)
    if token and cfg.auth_enabled:
        app.add_middleware(TokenAuthMiddleware, token=token, enabled=True)
        print(
            f"[auth] token auth ENABLED on http://{cfg.host}:{cfg.port}/mcp "
            f"(present as 'Authorization: Bearer <token>' or '?token=')"
        )
    else:
        print(
            f"[auth] token auth DISABLED — /mcp is open. Only safe on localhost."
        )

    # Temporary request diagnostics (method/path/status/host/origin/auth) to a
    # log file, to diagnose client connection failures.
    try:
        from .diagnostics import RequestDiagnosticMiddleware

        app.add_middleware(RequestDiagnosticMiddleware)
        print("[diag] request logging -> gemini_bridge_requests.log")
    except Exception as exc:  # noqa: BLE001
        print(f"[diag] could not enable request logging: {exc}")

    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="info")


if __name__ == "__main__":
    main()