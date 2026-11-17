"""Desktop console (tkinter) for gemini-local-bridge.

A native-window control panel that turns the headless bridge into a usable app:

  * **Workspace** — add/remove the directories the AI may touch (allowed_roots)
    with a native folder picker, so you never hand-edit JSON.
  * **Tools** — toggle filesystem / shell / system access and set the command
    safety policy.
  * **Connection** — show/copy the remote URL + bearer token, regenerate token.
  * **Server** — start/stop the HTTP bridge in a background thread with a live
    status indicator.
  * **Logs** — tail the audit + request logs inside the window.

Run with:  python gui.py        (or launch the packaged exe)
"""

from __future__ import annotations

import json
import os
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from gemini_local_bridge import autostart
from gemini_local_bridge.auth import generate_token
from gemini_local_bridge.config import Config
from gemini_local_bridge.tunnel import TunnelManager

CONFIG_PATH = Path("gemini_bridge.config.json")


# --------------------------------------------------------------------------- #
# Server controller: run uvicorn in a background thread, stoppable.
# --------------------------------------------------------------------------- #
class ServerController:
    """Start/stop the HTTP bridge in a daemon thread."""

    def __init__(self) -> None:
        self._server = None  # uvicorn.Server instance
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.last_error: str | None = None
     
    @property
    def running(self) -> bool:
        return (
             self._server is not None
             and not self._server.should_exit
             and self._thread is not None
            and self._thread.is_alive()
        )         

    def start(self, cfg: Config) -> None:
        with self._lock:
            if self.running:
                return
            import sys
            import uvicorn
            from starlette.responses import JSONResponse
            from gemini_local_bridge.server import create_server
            from gemini_local_bridge.auth import TokenAuthMiddleware, load_token

            if sys.stdout is None:
                sys.stdout = open(os.devnull, "w", encoding="utf-8")
            if sys.stderr is None:
                sys.stderr = open(os.devnull, "w", encoding="utf-8")

            server = create_server(cfg)

            @server.custom_route("/healthz", methods=["GET"])
            def _healthz(_request):
                return JSONResponse({"status": "ok", "server": cfg.server_name})

            app = server.streamable_http_app()

            token = load_token(cfg.auth_token)
            if token and cfg.auth_enabled:
                app.add_middleware(TokenAuthMiddleware, token=token, enabled=True)

            try:
                from gemini_local_bridge.diagnostics import RequestDiagnosticMiddleware
                app.add_middleware(RequestDiagnosticMiddleware)
            except Exception:
                pass

            config = uvicorn.Config(
                app,
                host=cfg.host,
                port=cfg.port,
                log_level="warning",
                log_config=None,
            )
            self._server = uvicorn.Server(config)
            self.last_error = None

            def _target() -> None:
                try:
                    self._server.run()
                except Exception as exc:
                    self.last_error = str(exc)
                    if self._server is not None:
                        self._server.should_exit = True

            self._thread = threading.Thread(target=_target, daemon=True)
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            if self._server is not None:
                self._server.should_exit = True
            self._server = None
            self._thread = None


# --------------------------------------------------------------------------- #
# Main application window
# --------------------------------------------------------------------------- #
class BridgeApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        from gemini_local_bridge import __version__

        self.title(f"Gemini Local Bridge — 控制台  v{__version__}")
        self.geometry("900x680")
        self.minsize(800, 600)

        # Modern 2026-era dark theme (sv_ttk + custom accent styles).
        from gemini_local_bridge.theme import apply_theme

        apply_theme(self, dark=True)

        # Window/taskbar icon from the branded asset.
        try:
            from gemini_local_bridge.icon import build_image
            from PIL import ImageTk

            self._app_icon = ImageTk.PhotoImage(build_image(256))
            self.iconphoto(True, self._app_icon)
        except Exception:  # noqa: BLE001 — icon is cosmetic; never block startup
            pass

        self.cfg = Config.load(str(CONFIG_PATH) if CONFIG_PATH.exists() else None)
        self.controller = ServerController()
        self._public_base = tk.StringVar(value="https://gb.tinybase.site")

        self._build_ui()
        self._load_into_form()
        self._refresh_status()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(1500, self._tick_status)

        # System tray: closing the window minimizes to the tray, not exit.
        self._tray = None
        self._really_quit = False
        from gemini_local_bridge.tray import TrayIcon

        self._tray = TrayIcon(self, self._show_window, self._quit_from_tray)
        self._tray.start()

    # ------------------------------------------------------------------ UI --
    def _build_ui(self) -> None:
        pad = {"padx": 8, "pady": 6}

        # ---- Top: server control bar (flat header) ----
        top = ttk.Frame(self)
        top.pack(fill="x", padx=14, pady=(14, 6))
        self.btn_toggle = ttk.Button(
            top, text="启动服务", style="Brand.TButton", command=self._toggle_server
        )
        self.btn_toggle.pack(side="left")
        self.lbl_status = ttk.Label(top, text="● 已停止", style="Bad.TLabel")
        self.lbl_status.pack(side="left", padx=16)
        ttk.Label(top, text="公网域名:").pack(side="left", padx=(16, 4))
        ttk.Entry(top, textvariable=self._public_base, width=26).pack(side="left")
        ttk.Button(
            top, text="主题", style="TButton", command=self._toggle_ui_theme
        ).pack(side="right")

        # ---- Connection info ----
        conn = ttk.LabelFrame(self, text="连接信息（填入 Gemini 远程 MCP）")
        conn.pack(fill="x", **pad)
        self.lbl_url = ttk.Label(conn, text="", font=("Consolas", 9))
        self.lbl_url.pack(fill="x", padx=8, pady=(6, 2))
        row = ttk.Frame(conn)
        row.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Label(row, text="Token:").pack(side="left")
        self.ent_token = ttk.Entry(row, width=44, show="")
        self.ent_token.pack(side="left", padx=6)
        ttk.Button(row, text="复制 URL", command=self._copy_url).pack(side="left", padx=2)
        ttk.Button(row, text="复制 Token", command=self._copy_token).pack(side="left", padx=2)
        ttk.Button(row, text="重新生成", command=self._regen_token).pack(side="left", padx=2)

        # ---- Notebook ----
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, **pad)

        # Tab 1: Workspace
        tab_ws = ttk.Frame(nb)
        nb.add(tab_ws, text="工作区")
        self._build_workspace_tab(tab_ws)

        # Tab 2: Tools
        tab_tools = ttk.Frame(nb)
        nb.add(tab_tools, text="工具与安全")
        self._build_tools_tab(tab_tools)

        # Tab 3: Logs
        tab_log = ttk.Frame(nb)
        nb.add(tab_log, text="日志")
        self._build_log_tab(tab_log)

        # Tab 4: File browser
        tab_files = ttk.Frame(nb)
        nb.add(tab_files, text="文件浏览")
        self._build_files_tab(tab_files)

        # Tab 5: Tunnel (cloudflared)
        tab_tunnel = ttk.Frame(nb)
        nb.add(tab_tunnel, text="隧道")
        self._build_tunnel_tab(tab_tunnel)

        # Tab 6: General settings (autostart)
        tab_set = ttk.Frame(nb)
        nb.add(tab_set, text="设置")
        self._build_settings_tab(tab_set)

        # ---- Bottom: apply ----
        bottom = ttk.Frame(self)
        bottom.pack(fill="x", **pad)
        ttk.Button(bottom, text="保存配置", command=self._save_config).pack(side="right")
        self.lbl_hint = ttk.Label(bottom, text="修改后点「保存配置」；改工作区/工具需重启服务生效。")
        self.lbl_hint.pack(side="left")

    def _build_workspace_tab(self, parent: ttk.Frame) -> None:
        bar = ttk.Frame(parent)
        bar.pack(fill="x", padx=8, pady=8)
        ttk.Button(bar, text="＋ 添加目录…", command=self._add_root).pack(side="left")
        ttk.Button(bar, text="－ 移除选中", command=self._remove_root).pack(side="left", padx=6)
        ttk.Label(bar, text="AI 只能访问以下目录（及其子目录）").pack(side="left", padx=12)

        self.lst_roots = tk.Listbox(parent, height=10, activestyle="dotbox")
        from gemini_local_bridge.theme import style_classic

        style_classic(self.lst_roots)
        self.lst_roots.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    def _build_tools_tab(self, parent: ttk.Frame) -> None:
        box = ttk.LabelFrame(parent, text="启用的工具类别")
        box.pack(fill="x", padx=8, pady=8)
        self.var_fs = tk.BooleanVar()
        self.var_shell = tk.BooleanVar()
        self.var_sys = tk.BooleanVar()
        ttk.Checkbutton(box, text="文件系统工具（读写/搜索/树）", variable=self.var_fs).pack(anchor="w", padx=8, pady=3)
        ttk.Checkbutton(box, text="Shell 工具（执行命令 / Python）", variable=self.var_shell).pack(anchor="w", padx=8, pady=3)
        ttk.Checkbutton(box, text="系统信息工具（系统/磁盘/进程）", variable=self.var_sys).pack(anchor="w", padx=8, pady=3)

        pol = ttk.LabelFrame(parent, text="命令安全策略")
        pol.pack(fill="x", padx=8, pady=8)
        self.var_mode = tk.StringVar()
        for val, label in [
            ("denylist", "denylist — 拦截危险命令（推荐）"),
            ("allowlist", "allowlist — 仅允许白名单命令"),
            ("unrestricted", "unrestricted — 不限制（危险）"),
        ]:
            ttk.Radiobutton(pol, text=label, value=val, variable=self.var_mode).pack(anchor="w", padx=8, pady=3)

        tok = ttk.LabelFrame(parent, text="Token 鉴权")
        tok.pack(fill="x", padx=8, pady=8)
        self.var_auth = tk.BooleanVar()
        ttk.Checkbutton(tok, text="启用 Token 鉴权（公网访问务必开启）", variable=self.var_auth).pack(anchor="w", padx=8, pady=3)

    def _build_log_tab(self, parent: ttk.Frame) -> None:
        bar = ttk.Frame(parent)
        bar.pack(fill="x", padx=8, pady=6)
        self.var_follow = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="自动滚动", variable=self.var_follow).pack(side="left")
        ttk.Button(bar, text="刷新", command=self._refresh_logs).pack(side="left", padx=6)
        ttk.Label(bar, text="最近 200 行").pack(side="left", padx=8)
        self.txt_log = tk.Text(parent, height=18, font=("Consolas", 9), state="disabled", wrap="none")
        from gemini_local_bridge.theme import style_classic

        style_classic(self.txt_log)
        self.txt_log.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    # ------------------------------------------------------- file browser --
    def _build_files_tab(self, parent: ttk.Frame) -> None:
        """Directory tree + file viewer, rooted at the first allowed root."""
        self._fb_root = (
            self.cfg.resolved_roots()[0] if self.cfg.resolved_roots() else Path.cwd()
        )
        bar = ttk.Frame(parent)
        bar.pack(fill="x", padx=8, pady=6)
        ttk.Button(bar, text="刷新", command=self._fb_refresh).pack(side="left")
        ttk.Button(bar, text="打开文件…", command=self._fb_open_dialog).pack(side="left", padx=6)
        self.lbl_fb_dir = ttk.Label(bar, text=str(self._fb_root), foreground="#555")
        self.lbl_fb_dir.pack(side="left", padx=12)

        paned = ttk.Panedwindow(parent, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        # Left: directory tree
        tree_frame = ttk.Frame(paned)
        self.tree_files = ttk.Treeview(tree_frame, show="tree", height=20)
        self.tree_files.pack(fill="both", expand=True)
        self.tree_files.bind("<<TreeviewSelect>>", self._fb_on_select)
        paned.add(tree_frame, weight=1)

        # Right: file viewer/editor
        edit_frame = ttk.Frame(paned)
        self.txt_file = tk.Text(edit_frame, font=("Consolas", 10), wrap="none")
        from gemini_local_bridge.theme import style_classic

        style_classic(self.txt_file)
        self.txt_file.pack(fill="both", expand=True)
        save_bar = ttk.Frame(edit_frame)
        save_bar.pack(fill="x")
        self.lbl_fb_file = ttk.Label(save_bar, text="(未打开文件)")
        self.lbl_fb_file.pack(side="left")
        ttk.Button(save_bar, text="保存", command=self._fb_save).pack(side="right")
        paned.add(edit_frame, weight=2)

        self._fb_current_file: Path | None = None
        self._fb_refresh()

    def _fb_refresh(self) -> None:
        """Populate the tree with the first allowed root's contents."""
        self.tree_files.delete(*self.tree_files.get_children())
        self.lbl_fb_dir.config(text=str(self._fb_root))
        try:
            for child in sorted(self._fb_root.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
                label = child.name + ("/" if child.is_dir() else "")
                self.tree_files.insert("", "end", text=label, values=[str(child)])
        except OSError:
            pass

    def _fb_on_select(self, _event) -> None:
        sel = self.tree_files.selection()
        if not sel:
            return
        path = Path(self.tree_files.item(sel[0], "values")[0])
        if path.is_file():
            self._fb_load_file(path)

    def _fb_load_file(self, path: Path) -> None:
        # Refuse to open binary files (exe/dll/images/...) as text — otherwise
        # they render as mojibake. Detect by scanning a chunk for NUL bytes.
        if self._looks_binary(path):
            messagebox.showinfo(
                "无法预览",
                f"「{path.name}」是二进制文件，无法以文本方式预览。\n\n"
                f"仅文本文件（.txt/.py/.json/.md 等）支持查看和编辑。",
            )
            return
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            messagebox.showerror("读取失败", str(exc))
            return
        self._fb_current_file = path
        self.lbl_fb_file.config(text=str(path))
        self.txt_file.delete("1.0", "end")
        self.txt_file.insert("1.0", text)

    @staticmethod
    def _looks_binary(path: Path) -> bool:
        """Heuristic: a file containing a NUL byte in its first 8 KB is binary."""
        try:
            with path.open("rb") as handle:
                chunk = handle.read(8192)
        except OSError:
            return False
        return b"\x00" in chunk

    def _fb_open_dialog(self) -> None:
        root = str(self._fb_root)
        path = filedialog.askopenfilename(initialdir=root, title="打开文件")
        if path:
            self._fb_load_file(Path(path))

    def _fb_save(self) -> None:
        if not self._fb_current_file:
            messagebox.showinfo("提示", "请先在左侧选择或打开一个文件。")
            return
        try:
            self._fb_current_file.write_text(
                self.txt_file.get("1.0", "end-1c"), encoding="utf-8"
            )
            messagebox.showinfo("已保存", str(self._fb_current_file))
        except OSError as exc:
            messagebox.showerror("保存失败", str(exc))

    # -------------------------------------------------------------- tunnel --
    def _build_tunnel_tab(self, parent: ttk.Frame) -> None:
        self.tunnel = TunnelManager()
        box = ttk.LabelFrame(parent, text="Cloudflare 隧道（cloudflared）")
        box.pack(fill="x", padx=8, pady=8)

        self.lbl_tunnel_status = ttk.Label(box, text="● 未启动", foreground="#b00")
        self.lbl_tunnel_status.pack(anchor="w", padx=8, pady=(6, 2))
        self.lbl_tunnel_host = ttk.Label(box, text=f"域名: {self.tunnel.hostname or '(未检测到)'}")
        self.lbl_tunnel_host.pack(anchor="w", padx=8)

        btns = ttk.Frame(box)
        btns.pack(fill="x", padx=8, pady=6)
        ttk.Button(btns, text="启动隧道", command=self._tunnel_start).pack(side="left")
        ttk.Button(btns, text="停止隧道", command=self._tunnel_stop).pack(side="left", padx=6)

        info = ttk.LabelFrame(parent, text="说明")
        info.pack(fill="x", padx=8, pady=8)
        ttk.Label(
            info,
            text=(
                "隧道读取 ~/.cloudflared/gembridge.yml（含 tunnel id + ingress 域名）。\n"
                "需先安装 cloudflared 并已 cloudflared tunnel login。\n"
                "启动后，公网地址 = https://<域名>/mcp?token=<token>。"
            ),
            justify="left",
        ).pack(anchor="w", padx=8, pady=6)

    def _tunnel_start(self) -> None:
        ok, msg = self.tunnel.start()
        if not ok:
            messagebox.showerror("启动隧道失败", msg)
        self._tunnel_refresh_status()

    def _tunnel_stop(self) -> None:
        self.tunnel.stop()
        self._tunnel_refresh_status()

    def _tunnel_refresh_status(self) -> None:
        running = self.tunnel.running
        self.lbl_tunnel_status.config(
            text="● 隧道运行中（公网在线）" if running else "● 隧道未启动",
            style="Ok.TLabel" if running else "Bad.TLabel",
        )

    # ------------------------------------------------------------ settings --
    def _build_settings_tab(self, parent: ttk.Frame) -> None:
        box = ttk.LabelFrame(parent, text="开机与托盘")
        box.pack(fill="x", padx=8, pady=8)
        self.var_autostart = tk.BooleanVar(value=autostart.is_enabled())
        ttk.Checkbutton(
            box,
            text="开机自动启动（登录时最小化到托盘）",
            variable=self.var_autostart,
            command=self._toggle_autostart,
        ).pack(anchor="w", padx=8, pady=6)

        self.var_start_tunnel = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            box,
            text="启动服务时同时启动 Cloudflare 隧道",
            variable=self.var_start_tunnel,
        ).pack(anchor="w", padx=8, pady=3)

        tip = ttk.LabelFrame(parent, text="提示")
        tip.pack(fill="x", padx=8, pady=8)
        ttk.Label(
            tip,
            text=(
                "关闭窗口会最小化到系统托盘（图标仍在，服务继续运行）。\n"
                "右键托盘图标可显示窗口或退出。"
            ),
            justify="left",
        ).pack(anchor="w", padx=8, pady=6)

    def _toggle_autostart(self) -> None:
        if self.var_autostart.get():
            ok, msg = autostart.enable()
        else:
            ok, msg = autostart.disable()
        if not ok:
            messagebox.showerror("设置失败", msg)
            self.var_autostart.set(autostart.is_enabled())
        else:
            self.lbl_hint.config(text=msg)

    # ------------------------------------------------------- form binding --
    def _load_into_form(self) -> None:
        self.lst_roots.delete(0, "end")
        for r in self.cfg.allowed_roots:
            self.lst_roots.insert("end", str(r))
        self.var_fs.set(self.cfg.enable_filesystem)
        self.var_shell.set(self.cfg.enable_shell)
        self.var_sys.set(self.cfg.enable_system)
        self.var_mode.set(self.cfg.command_policy.mode)
        self.var_auth.set(bool(self.cfg.auth_token) and self.cfg.auth_enabled)
        self.ent_token.delete(0, "end")
        self.ent_token.insert(0, self.cfg.auth_token or "(未设置)")
        self._update_url()

    def _collect_form(self) -> None:
        self.cfg.allowed_roots = list(self.lst_roots.get(0, "end"))
        self.cfg.enable_filesystem = self.var_fs.get()
        self.cfg.enable_shell = self.var_shell.get()
        self.cfg.enable_system = self.var_sys.get()
        self.cfg.command_policy.mode = self.var_mode.get()
        tok = self.ent_token.get().strip()
        self.cfg.auth_token = "" if tok == "(未设置)" else tok
        self.cfg.auth_enabled = self.var_auth.get()

    def _save_config(self) -> None:
        self._collect_form()
        CONFIG_PATH.write_text(
            json.dumps(self.cfg.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        self._update_url()
        messagebox.showinfo("已保存", f"配置已写入 {CONFIG_PATH}\n若服务运行中，重启后生效。")

    # ------------------------------------------------------- workspace ops --
    def _add_root(self) -> None:
        d = filedialog.askdirectory(title="选择允许 AI 访问的工作目录")
        if not d:
            return
        d = os.path.normpath(d)
        existing = list(self.lst_roots.get(0, "end"))
        if d not in existing:
            self.lst_roots.insert("end", d)

    def _remove_root(self) -> None:
        sel = self.lst_roots.curselection()
        for i in reversed(sel):
            self.lst_roots.delete(i)

    # --------------------------------------------------------- connection --
    def _remote_url(self) -> str:
        base = self._public_base.get().strip().rstrip("/")
        tok = self.ent_token.get().strip()
        url = f"{base}/mcp"
        if tok and tok != "(未设置)":
            url += f"?token={tok}"
        return url

    def _update_url(self) -> None:
        self.lbl_url.config(text=self._remote_url())

    def _copy_url(self) -> None:
        self._to_clipboard(self._remote_url())

    def _copy_token(self) -> None:
        tok = self.ent_token.get().strip()
        if tok and tok != "(未设置)":
            self._to_clipboard(tok)

    def _to_clipboard(self, text: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(text)
        self.lbl_hint.config(text="已复制到剪贴板。")
        self.after(2500, lambda: self.lbl_hint.config(text=""))

    def _regen_token(self) -> None:
        if not messagebox.askyesno("重新生成 Token", "旧的 token 将失效，Gemini 需要用新 URL 重新连接。继续？"):
            return
        new = generate_token()
        self.ent_token.delete(0, "end")
        self.ent_token.insert(0, new)
        self.var_auth.set(True)
        self._update_url()

    # ------------------------------------------------------------- server --
    def _toggle_server(self) -> None:
        if self.controller.running:
            self.controller.stop()
            if getattr(self, "tunnel", None) and self.tunnel.running:
                self.tunnel.stop()
                self._tunnel_refresh_status()
        else:
            self._collect_form()
            CONFIG_PATH.write_text(
                json.dumps(self.cfg.model_dump(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            try:
                self.controller.start(self.cfg)
            except Exception as exc:  # noqa: BLE001
                messagebox.showerror("启动失败", str(exc))
                return
            # Optionally bring up the Cloudflare tunnel together with the bridge.
            if getattr(self, "var_start_tunnel", None) and self.var_start_tunnel.get():
                ok, msg = self.tunnel.start()
                self._tunnel_refresh_status()
                if not ok:
                    messagebox.showwarning("隧道未启动", msg)
        self._refresh_status()

    def _refresh_status(self) -> None:
        running = self.controller.running
        self.btn_toggle.config(
            text="停止服务" if running else "启动服务",
            style="Danger.TButton" if running else "Brand.TButton",
        )
        self.lbl_status.config(
            text="● 运行中" if running else "● 已停止",
            style="Ok.TLabel" if running else "Bad.TLabel",
        )

    def _toggle_ui_theme(self) -> None:
        from gemini_local_bridge.theme import toggle_theme

        toggle_theme(self)

    def _tick_status(self) -> None:
        self._refresh_status()
        if self.var_follow.get():
            self._refresh_logs()
        self.after(1500, self._tick_status)

    # ---------------------------------------------------------------- logs --
    def _refresh_logs(self) -> None:
        lines: list[str] = []
        for name in ("gemini_bridge_requests.log", "gemini_bridge_audit.jsonl"):
            p = Path(name)
            if p.is_file():
                try:
                    tail = p.read_text(encoding="utf-8", errors="replace").splitlines()[-100:]
                except OSError:
                    continue
                lines.append(f"===== {name} =====")
                lines.extend(tail)
        text = "\n".join(lines)
        self.txt_log.config(state="normal")
        self.txt_log.delete("1.0", "end")
        self.txt_log.insert("1.0", text)
        self.txt_log.config(state="disabled")
        if self.var_follow.get():
            self.txt_log.see("end")

    # --------------------------------------------------------------- close --
    def _show_window(self) -> None:
        """Restore the window from the tray."""
        self.deiconify()
        self.lift()
        self.focus_force()

    def _quit_from_tray(self) -> None:
        """Full quit requested from the tray menu."""
        self._really_quit = True
        self._shutdown()

    def _on_close(self) -> None:
        """Window X button: minimize to tray (keep running) instead of exiting."""
        self.withdraw()

    def _shutdown(self) -> None:
        """Stop everything and destroy the window."""
        try:
            self.controller.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            if getattr(self, "tunnel", None) and self.tunnel.running:
                self.tunnel.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._tray is not None:
                self._tray.stop()
        except Exception:  # noqa: BLE001
            pass
        self.destroy()


def main() -> None:
    app = BridgeApp()
    app.mainloop()


if __name__ == "__main__":
    main()
