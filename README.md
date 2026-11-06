# gemini-local-bridge

把 **Gemini**（网页版 / 桌面版 / CLI）连接到**本地系统资源**的桥梁 —— 让网页版 AI 能直接读写本地文件、执行命令、查看系统信息。

灵感来自 [yyjeqhc/webcodex](https://github.com/yyjeqhc/webcodex) 的核心思想：**用一个本地常驻服务，把云端 AI 和本地机器的能力打通**，使运行在浏览器里的模型可以操作你磁盘上的真实资源。webcodex 面向 Codex，本项目面向 Gemini，并采用 **MCP（Model Context Protocol）** 这一开放标准来实现。

---

## 它解决什么问题

网页版 AI 跑在云端沙箱里，看不到你本地的文件、跑不了你机器上的命令。这个项目在你本机启动一个小型 MCP 服务器，把本地能力封装成一组**工具（tools）**暴露给 Gemini：

```
Gemini (web / desktop / CLI)
        │  MCP (stdio 或 HTTP)
        ▼
gemini-local-bridge  ──►  文件系统 / Shell / 系统信息
        │
        └── 安全护栏：路径沙箱 + 命令策略 + 审计日志
```

## 功能

**文件系统（12 个工具）**
`fs_list` `fs_read` `fs_write` `fs_append` `fs_edit` `fs_search` `fs_tree` `fs_info` `fs_mkdir` `fs_move` `fs_copy` `fs_delete`

**Shell / 系统（6 个工具）**
`shell_run` `shell_python` `sys_info` `sys_disk` `sys_env` `sys_processes`

**Git（8 个工具）**
`git_status` `git_log` `git_diff` `git_branches` `git_add` `git_commit` `git_push` `git_pull`

**Web / 内容（3 个工具）**
`web_fetch`（抓取网页文本）`image_info`（图片元数据）`pdf_text`（提取 PDF 文本）

> 共 **29 个工具**，可在配置或控制台里按类别开关。

所有工具调用都会：
- 通过**路径沙箱**校验 —— 任何路径解析后必须落在配置的 `allowed_roots` 内，否则拒绝；
- 通过**命令策略**校验（shell 命令）—— 支持 `denylist` / `allowlist` / `unrestricted` 三种模式 + 危险模式正则拦截；
- 写入**审计日志**（JSONL），人类可随时复查 AI 到底做了什么。

## 安装

**方式 A：直接运行打包好的 exe（无需 Python）**

构建产物在 `dist/gemini-local-bridge/gemini-local-bridge.exe`，已自带全部运行时。直接双击或在命令行运行即可（见下方「打包成可运行软件」）。

**方式 B：从源码运行（需要 Python 3.10+）**

```bash
pip install -r requirements.txt
```

## 运行

**最简单：双击 exe 打开桌面控制台**

直接双击 `dist\gemini-local-bridge\gemini-local-bridge.exe`，会弹出图形界面控制台，无需命令行：
- **工作区**：点「＋ 添加目录…」用文件夹选择器添加允许 AI 访问的目录，再也不用手改 JSON
- **连接信息**：显示完整远程 URL + Token，一键复制；可重新生成 Token
- **工具与安全**：勾选启用 文件系统 / Shell / 系统 / Git / Web / 内容 工具，选命令安全策略
- **日志**：实时查看调用日志
- **文件浏览**：目录树 + 内置编辑器，直接在界面里查看/编辑允许目录内的文件
- **隧道**：一键启动/停止 Cloudflare 隧道，显示公网在线状态
- **设置**：开机自动启动（登录时最小化到托盘）、随服务启动隧道
- **系统托盘**：关闭窗口最小化到托盘（服务继续运行），右键托盘图标可显示窗口或退出
- 点「启动服务」即在本机起 HTTP 桥接（配合隧道即可公网访问）

> 命令行带参数时仍走无界面模式（如 `--transport stdio` 给 Gemini CLI，`--transport streamable-http` 给隧道），双击（无参数）才弹界面。

### 1) Gemini CLI（stdio，推荐起步）

复制 `gemini_cli.settings.example.json` 的 `mcpServers` 段到 Gemini CLI 的 `settings.json`：

```json
{
  "mcpServers": {
    "gemini-local-bridge": {
      "command": "python",
      "args": ["run_server.py", "--transport", "stdio"],
      "cwd": "C:\\path\\to\\gemini_MCP"
    }
  }
}
```

重启 Gemini CLI，输入 `/mcp` 应能看到 `gemini-local-bridge` 已连接及其 18 个工具。

### 2) Gemini 网页版 / AI Studio（HTTP + Token 鉴权）

网页端无法直接拉起本地进程，所以走 HTTP 传输。**远程访问务必设置 token**，否则 `/mcp` 是开放的：

```bash
# 生成一个 bearer token
python remote_mcp.py gen-token
# PowerShell 设置环境变量（本会话有效）
$env:GEMINI_BRIDGE_TOKEN = "<上一步的 token>"

python run_server.py --transport streamable-http --port 8765
# 服务监听 http://127.0.0.1:8765/mcp（需 token 才能访问）
# http://127.0.0.1:8765/healthz 无需 token，供探活
```

带 token 访问的两种写法（Gemini 连接器一般用 URL，可直接带 `?token=`）：

```
Authorization: Bearer <token>
https://你的域名/mcp?token=<token>
```

> **注意**：纯 `127.0.0.1` 仅本机可达。要让网页版跨网络访问，请用下方的 **Cloudflare Tunnel** 方案，并配合 token 鉴权。

### 3) 远程访问：用 Cloudflare 隧道把本机映射到你的域名

你有 Cloudflare 托管的域名，最干净的方式是用 **Cloudflare Tunnel（`cloudflared`）**：无需公网 IP、无需开防火墙端口、自动 HTTPS。

**快速临时隧道（一行命令，得到 `*.trycloudflare.com` 临时地址）：**

```bash
# 终端 A：启动桥接（带 token）
$env:GEMINI_BRIDGE_TOKEN="<token>"; python remote_mcp.py serve --port 8765
# 终端 B：启动隧道
cloudflared tunnel --url http://localhost:8765
# 终端 B 会打印形如 https://xxxx.trycloudflare.com 的地址
```

**绑定到自己的域名（`mcp.your-domain.com`，稳定长期）：**

```bash
cloudflared tunnel login
cloudflared tunnel create gemini-local-bridge
cloudflared tunnel route dns gemini-local-bridge mcp.your-domain.com
cloudflared tunnel run --url http://localhost:8765 gemini-local-bridge
```

然后把 Gemini 网页版「连接远程 MCP」的 URL 填成：

```
https://mcp.your-domain.com/mcp?token=<你的 token>
```

用 `remote_mcp.py` 直接打印这个可粘贴的 URL：

```bash
python remote_mcp.py print-connect --public-url https://mcp.your-domain.com
```

> **更稳的做法**：在 Cloudflare Zero Trust 里给该主机名加 **Cloudflare Access** 策略（SSO/邮箱验证），token 之上再套一层身份认证。`cloudflared` 也支持作为 Windows 服务常驻（`cloudflared service install`）。

### 4) 生成默认配置

```bash
python run_server.py --init-config   # 写出 gemini_bridge.config.json
```

## 配置

复制 `gemini_bridge.config.example.json` 为 `gemini_bridge.config.json`（或用 `GEMINI_BRIDGE_CONFIG` 环境变量指定路径）。关键字段：

| 字段 | 说明 |
|---|---|
| `allowed_roots` | **最重要的安全项**。AI 只能访问这些目录（含其子目录）。相对路径相对启动目录解析。 |
| `transport` | `stdio` / `streamable-http` / `sse` |
| `host`, `port` | HTTP 传输的监听地址/端口（默认 `127.0.0.1:8765`） |
| `auth_token` | **远程访问必设**。HTTP/SSE 每个请求需带此 bearer token（`Authorization: Bearer` 或 `?token=`）。也可用环境变量 `GEMINI_BRIDGE_TOKEN`。留空则不鉴权（仅限 localhost）。 |
| `auth_enabled` | 设为 `false` 可临时关闭 token 校验（仅调试用） |
| `enable_filesystem` / `enable_shell` / `enable_system` | 三类能力的总开关，可只暴露文件访问 |
| `command_policy.mode` | `denylist`（默认，禁用危险命令）/ `allowlist`（仅允许白名单）/ `unrestricted` |
| `command_policy.deny` / `allow` | 命令名黑/白名单 |
| `command_policy.block_patterns` | 危险正则，无论命令名如何都拦截（如 `rm -rf`） |
| `audit_log_path` | 审计日志文件，设为 `null` 关闭 |

命令策略默认拦截 `rm`/`del`/`format`/`shutdown`/`reg`/`mv` 等破坏性命令，并用正则额外拦 `rm -rf`、`del /s`、fork 炸弹、写裸设备等。

## 安全模型（务必阅读）

这是一个**故意给 AI 开了一扇通往你磁盘和 Shell 的门**的工具。默认护栏能挡住绝大多数误操作，但请理解：

1. **路径沙箱**：所有文件操作经 `resolve_in_sandbox` 解析，符号链接也会被 resolve，逃逸到根外即被拒。
2. **命令策略**：`shell_run` 走 denylist + 正则双重校验；`shell_python` 对代码体套用 block_patterns。
3. **远程鉴权**：HTTP 模式用共享 bearer token 保护 `/mcp`（`auth_token` 或 `GEMINI_BRIDGE_TOKEN`）。`/healthz` 探活端点无需 token。跨网络请务必设置 token，并用 Cloudflare Tunnel + TLS，必要时叠加 Cloudflare Access。**不要**关闭鉴权裸露到公网。
4. **审计**：默认写 `gemini_bridge_audit.jsonl`，每条记录含时间、工具、参数、结果。
5. **最小暴露**：只读场景可设 `enable_shell=false`、`enable_system=false`，只留文件工具。

## 打包成可运行软件

用 PyInstaller 打包成免 Python 的独立可执行程序：

```bash
pip install pyinstaller
pyinstaller gemini_local_bridge.spec --noconfirm --clean
```

产物在 `dist/gemini-local-bridge/`：

```
gemini-local-bridge.exe      # 主程序（自带全部运行时）
gemini_bridge.config.json    # 可选：放在 exe 同目录即自动加载
```

直接运行（等价于源码版的 `run_server.py`）：

```bash
# stdio（Gemini CLI）
gemini-local-bridge.exe --transport stdio
# HTTP 远程（Gemini 网页版），token 从环境变量读
$env:GEMINI_BRIDGE_TOKEN="<token>"; gemini-local-bridge.exe --transport streamable-http --port 8765
# 生成默认配置
gemini-local-bridge.exe --init-config
```

> **提示**：把 `gemini-local-bridge.exe` 和一个写好 `allowed_roots` + `auth_token` 的 `gemini_bridge.config.json` 放同一目录，就能即开即用。想让隧道常驻，把 `cloudflared` 也注册为 Windows 服务即可。

## 项目结构

```
gemini_MCP/
├── gemini_local_bridge/
│   ├── config.py          # 配置模型（allowed_roots、command_policy、auth_token…）
│   ├── safety.py          # 路径沙箱、命令策略、审计日志
│   ├── auth.py            # HTTP bearer-token 鉴权中间件
│   ├── fs_tools.py        # 文件系统工具
│   ├── system_tools.py    # Shell + 系统信息工具
│   ├── extra_tools.py     # Git / Web / 图片/PDF 工具
│   ├── tunnel.py          # Cloudflare 隧道管理（启动/停止/状态）
│   ├── autostart.py       # Windows 开机自启（注册表 Run）
│   ├── tray.py            # 系统托盘图标（pystray）
│   ├── diagnostics.py     # 请求诊断日志中间件
│   ├── server.py          # FastMCP 服务器 + 工具注册 + 带鉴权的 HTTP 启动
│   └── __main__.py        # python -m 入口
├── run_server.py          # 独立启动器（无参数=开GUI，带参数=无界面模式）
├── gui.py                 # 桌面控制台（tkinter）：工作区/工具/文件浏览/隧道/日志
├── remote_mcp.py          # 远程接入助手（gen-token / serve / tunnel / print-connect）
├── smoke_test.py          # 端到端自测（19 项）
├── gemini_local_bridge.spec  # PyInstaller 打包配置
├── requirements.txt
├── gemini_bridge.config.example.json
└── gemini_cli.settings.example.json
```

## 自测

```bash
python smoke_test.py    # 本地工具层：19 项（文件/沙箱/命令策略）
```

**远程 HTTP 端到端自测**（对打包后的 exe 验证完整 MCP 会话：initialize → tools/call）：

```bash
# 终端 A：启动 exe（挑一个空闲端口）
$env:GEMINI_BRIDGE_TOKEN="e2etoken"; dist\gemini-local-bridge\gemini-local-bridge.exe --transport streamable-http --port 8820 --root .
# 终端 B：跑远程客户端测试
$env:TOKEN="e2etoken"; $env:BRIDGE_URL="http://127.0.0.1:8820"; python e2e_remote_test.py
```

覆盖：initialize 拿到 session id、远程 `fs_write`/`fs_read` 往返、沙箱逃逸被拒、危险 shell 命令被拒。
```

覆盖：文件读写/编辑/搜索/树/复制/移动/删除的完整往返，以及**沙箱逃逸拦截**（`~/.ssh`、`../`、绝对路径越界）和**命令策略拦截**（`echo` 放行、`del`/`rm` 拒绝）。

## 设计取舍

- **为什么用 MCP 而不是自造 HTTP API**：MCP 是 Anthropic 主导、已被 Gemini CLI / AI Studio 等广泛采纳的开放标准。用 MCP 意味着同一套工具能同时被 Gemini、Claude、Cursor 等多种客户端复用，无需为每个 AI 重写适配层 —— 这正是 webcodex 思想的通用化。
- **为什么工具返回 `{"ok": false, "error": ...}` 而不是抛异常**：把「拒绝」作为模型可读的正常结果返回，模型能据此调整行为，而不是让调用链崩掉。
- **为什么 `fs_edit` 用字节读写**：避免 Windows 上 `read_text`/`write_text` 悄悄把 `\n` 改成 `\r\n`，破坏文件原有换行。

## License

MIT