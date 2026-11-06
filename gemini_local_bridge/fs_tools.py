"""Filesystem tools exposed to Gemini.

Every tool resolves its paths through :func:`resolve_in_sandbox`, so no matter
what the model asks for, it cannot touch anything outside the configured
allowed roots. Tools return plain dicts so the results serialise cleanly into
MCP tool output.
"""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from typing import Iterable

from .config import Config
from .safety import AuditLog, SafetyError, resolve_in_sandbox


class FileSystemTools:
    """Bundle of filesystem operations bound to a config + audit log."""

    def __init__(self, cfg: Config, audit: AuditLog):
        self.cfg = cfg
        self.audit = audit

    @property
    def roots(self):
        return self.cfg.resolved_roots()

    def _resolve(self, path: str) -> Path:
        return resolve_in_sandbox(path, self.roots)

    # -- listing / inspection ------------------------------------------------ #
    def list_dir(self, path: str = ".") -> dict:
        p = self._resolve(path)
        if not p.exists():
            raise SafetyError(f"Not found: {p}")
        if not p.is_dir():
            raise SafetyError(f"Not a directory: {p}")
        entries = []
        for child in sorted(p.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower())):
            entries.append(self._entry(child))
            if len(entries) >= self.cfg.max_entries:
                break
        result = {"path": str(p), "count": len(entries), "entries": entries}
        self.audit.record("fs_list", {"path": path}, "ok")
        return result

    def _entry(self, child: Path) -> dict:
        try:
            is_dir = child.is_dir()
            size = 0 if is_dir else child.stat().st_size
        except OSError:
            is_dir, size = False, -1
        return {"name": child.name, "type": "dir" if is_dir else "file", "size": size}

    def get_info(self, path: str) -> dict:
        p = self._resolve(path)
        if not p.exists():
            raise SafetyError(f"Not found: {p}")
        st = p.stat()
        self.audit.record("fs_info", {"path": path}, "ok")
        return {
            "path": str(p),
            "type": "dir" if p.is_dir() else "file",
            "size_bytes": st.st_size,
            "modified": st.st_mtime,
        }

    def read_file(self, path: str, max_bytes: int | None = None) -> dict:
        p = self._resolve(path)
        if not p.is_file():
            raise SafetyError(f"Not a file: {p}")
        limit = min(max_bytes or self.cfg.max_read_bytes, self.cfg.max_read_bytes)
        size = p.stat().st_size
        with p.open("rb") as fh:
            data = fh.read(limit)
        truncated = size > limit
        try:
            text = data.decode("utf-8")
            encoding = "utf-8"
        except UnicodeDecodeError:
            text = data.decode("utf-8", errors="replace")
            encoding = "utf-8 (lossy)"
        self.audit.record("fs_read", {"path": path}, "ok", f"{size} bytes")
        return {
            "path": str(p),
            "size_bytes": size,
            "truncated": truncated,
            "encoding": encoding,
            "content": text,
        }

    def search(
        self,
        root: str,
        pattern: str,
        glob: str = "*",
        max_results: int = 100,
        include_hidden: bool = False,
    ) -> dict:
        """Grep-like content search under *root* for files matching *glob*."""
        base = self._resolve(root)
        if not base.exists():
            raise SafetyError(f"Not found: {base}")
        needle = pattern
        matches: list[dict] = []
        scanned = 0
        for file in self._iter_files(base, include_hidden):
            if not fnmatch.fnmatch(file.name, glob):
                continue
            scanned += 1
            if scanned > 5000:
                break
            try:
                if file.stat().st_size > self.cfg.max_read_bytes:
                    continue
                with file.open("r", encoding="utf-8", errors="ignore") as fh:
                    for lineno, line in enumerate(fh, 1):
                        if needle in line:
                            matches.append(
                                {
                                    "file": str(file),
                                    "line": lineno,
                                    "text": line.rstrip()[:300],
                                }
                            )
                            if len(matches) >= max_results:
                                self.audit.record(
                                    "fs_search", {"root": root, "pattern": pattern}, "ok", "max hit"
                                )
                                return {
                                    "root": str(base),
                                    "scanned_files": scanned,
                                    "matches": matches,
                                    "truncated": True,
                                }
            except OSError:
                continue
        self.audit.record("fs_search", {"root": root, "pattern": pattern}, "ok")
        return {
            "root": str(base),
            "scanned_files": scanned,
            "matches": matches,
            "truncated": False,
        }

    def tree(self, path: str = ".", depth: int = 3) -> dict:
        base = self._resolve(path)
        if not base.is_dir():
            raise SafetyError(f"Not a directory: {base}")
        depth = max(1, min(depth, 8))
        entries: list[str] = []

        def walk(d: Path, level: int, prefix: str) -> None:
            if level > depth or len(entries) >= self.cfg.max_entries:
                return
            children = sorted(
                d.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower())
            )
            for i, child in enumerate(children):
                if len(entries) >= self.cfg.max_entries:
                    entries.append(prefix + "…")
                    return
                connector = "└── " if i == len(children) - 1 else "├── "
                entries.append(prefix + connector + child.name)
                if child.is_dir():
                    ext = "    " if i == len(children) - 1 else "│   "
                    walk(child, level + 1, prefix + ext)

        walk(base, 1, "")
        self.audit.record("fs_tree", {"path": path}, "ok")
        return {"path": str(base), "tree": "\n".join(entries)}

    # -- mutation ------------------------------------------------------------ #
    def write_file(self, path: str, content: str, overwrite: bool = True) -> dict:
        p = self._resolve(path)
        if p.exists() and not overwrite:
            raise SafetyError(f"Refusing to overwrite existing file: {p}")
        if p.exists() and p.is_dir():
            raise SafetyError(f"Path is a directory: {p}")
        encoded = content.encode("utf-8")
        if len(encoded) > self.cfg.max_write_bytes:
            raise SafetyError(
                f"Content too large ({len(encoded)} > {self.cfg.max_write_bytes} bytes)."
            )
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(encoded)
        self.audit.record("fs_write", {"path": path}, "ok", f"{len(encoded)} bytes")
        return {"path": str(p), "bytes_written": len(encoded)}

    def append_file(self, path: str, content: str) -> dict:
        p = self._resolve(path)
        encoded = content.encode("utf-8")
        if len(encoded) > self.cfg.max_write_bytes:
            raise SafetyError("Content too large.")
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("ab") as fh:
            fh.write(encoded)
        self.audit.record("fs_append", {"path": path}, "ok", f"+{len(encoded)} bytes")
        return {"path": str(p), "bytes_appended": len(encoded)}

    def edit_file(
        self, path: str, old_string: str, new_string: str, replace_all: bool = False
    ) -> dict:
        p = self._resolve(path)
        if not p.is_file():
            raise SafetyError(f"Not a file: {p}")
        # Read/write raw bytes so we never silently rewrite the file's line
        # endings (text-mode helpers translate \n <-> \r\n on Windows).
        text = p.read_bytes().decode("utf-8")
        count = text.count(old_string)
        if count == 0:
            raise SafetyError("old_string not found in file.")
        if not replace_all and count > 1:
            raise SafetyError(
                f"old_string appears {count} times; pass replace_all=true to replace all."
            )
        new_text = (
            text.replace(old_string, new_string)
            if replace_all
            else text.replace(old_string, new_string, 1)
        )
        p.write_bytes(new_text.encode("utf-8"))
        self.audit.record("fs_edit", {"path": path}, "ok", f"{count} replacement(s)")
        return {"path": str(p), "replacements": count if replace_all else 1}

    def mkdir(self, path: str) -> dict:
        p = self._resolve(path)
        p.mkdir(parents=True, exist_ok=True)
        self.audit.record("fs_mkdir", {"path": path}, "ok")
        return {"path": str(p), "created": True}

    def _check_pair(self, src: str, dst: str) -> tuple[Path, Path]:
        s = self._resolve(src)
        d = self._resolve(dst)
        return s, d

    def move(self, src: str, dst: str) -> dict:
        s, d = self._check_pair(src, dst)
        if not s.exists():
            raise SafetyError(f"Source not found: {s}")
        d.parent.mkdir(parents=True, exist_ok=True)
        os.replace(s, d)
        self.audit.record("fs_move", {"src": src, "dst": dst}, "ok")
        return {"src": str(s), "dst": str(d)}

    def copy(self, src: str, dst: str) -> dict:
        import shutil

        s, d = self._check_pair(src, dst)
        if not s.exists():
            raise SafetyError(f"Source not found: {s}")
        d.parent.mkdir(parents=True, exist_ok=True)
        if s.is_dir():
            shutil.copytree(s, d, dirs_exist_ok=True)
        else:
            shutil.copy2(s, d)
        self.audit.record("fs_copy", {"src": src, "dst": dst}, "ok")
        return {"src": str(s), "dst": str(d)}

    def delete(self, path: str, recursive: bool = False) -> dict:
        import shutil

        p = self._resolve(path)
        if not p.exists():
            raise SafetyError(f"Not found: {p}")
        if p.is_dir():
            if not recursive:
                try:
                    p.rmdir()
                except OSError:
                    raise SafetyError(
                        "Directory not empty; pass recursive=true to delete contents."
                    )
            else:
                shutil.rmtree(p)
        else:
            p.unlink()
        self.audit.record("fs_delete", {"path": path}, "ok", f"recursive={recursive}")
        return {"path": str(p), "deleted": True}

    # -- helpers ------------------------------------------------------------- #
    def _iter_files(self, base: Path, include_hidden: bool) -> Iterable[Path]:
        for dirpath, dirnames, filenames in os.walk(base):
            if not include_hidden:
                dirnames[:] = [
                    d for d in dirnames if not d.startswith(".")
                ]
            for name in filenames:
                yield Path(dirpath) / name