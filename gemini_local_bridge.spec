# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for gemini-local-bridge.

Build with:
    pyinstaller gemini_local_bridge.spec

Produces dist/gemini-local-bridge/ containing gemini-local-bridge.exe plus the
bundled runtime. Config is read from (in order): --config arg, GEMINI_BRIDGE_CONFIG
env var, a gemini_bridge.config.json next to the exe, then built-in defaults.
"""

from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

hiddenimports = [
    "uvicorn",
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "anyio",
    "httpx",
    "mcp",
    "mcp.server.fastmcp",
    "pydantic",
    # Desktop console + extra tools
    "pystray",
    "pystray._win32",
    "sv_ttk",
    "PIL",
    "PIL.Image",
    "PIL.ImageDraw",
    "pypdf",
    "tkinter",
]

# mcp / anyio may ship package data; collect it defensively.
datas = []
datas += collect_data_files("mcp", include_py_files=True)

a = Analysis(
    ["run_server.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="gemini-local-bridge",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # GUI app: no console window on launch
    icon="gemini-local-bridge.ico",  # branded app icon
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="gemini-local-bridge",
)