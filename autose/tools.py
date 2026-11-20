"""Per-agent tools with a path jail and a sandboxed code runner (ADR-005, ADR-009)."""
from __future__ import annotations

import fnmatch
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .llm import Tool

DENY_PARTS = {".git", ".github", "autose", ".autose-run", "__pycache__", ".venv",
              "venv", ".mypy_cache", ".pytest_cache", "node_modules"}
MAX_OUT = 8000
SANDBOX_USER = os.environ.get("AUTOSE_SANDBOX_USER", "")  # set by the workflow


def _trunc(s: str, n: int = MAX_OUT) -> str:
    if len(s) <= n:
        return s
    half = n // 2
    return s[:half] + f"\n…[{len(s) - n} chars truncated]…\n" + s[-half:]


class Jail:
    """Resolves agent-supplied paths inside `root`, refusing escapes."""

    def __init__(self, root: Path, writable: list[str] | None = None):
        self.root = root.resolve()
        self.writable = writable or []

    def resolve(self, rel: str) -> Path:
        rel = (rel or ".").strip().lstrip("/")
        p = (self.root / rel).resolve()
        if p != self.root and self.root not in p.parents:
            raise PermissionError(f"path escapes the repository: {rel}")
        parts = set(p.relative_to(self.root).parts)
        if parts & DENY_PARTS:
            raise PermissionError(f"path is not accessible to agents: {rel}")
        return p

    def check_writable(self, rel: str) -> Path:
        p = self.resolve(rel)
        relp = p.relative_to(self.root).as_posix()
        if not any(fnmatch.fnmatch(relp, pat) for pat in self.writable):
            raise PermissionError(
                f"write to {relp} not allowed; allowed patterns: {self.writable}")
        return p


def sandbox_run(argv: list[str], cwd: Path, timeout: int,
                extra_env: dict | None = None) -> tuple[int, str]:
    """Run model-influenced code with an empty environment, as an
    unprivileged user when available (no API key, no GitHub token)."""
    env = {"PATH": os.path.dirname(sys.executable) + ":/usr/bin:/bin",
           "HOME": tempfile.gettempdir(), "PYTHONDONTWRITEBYTECODE": "1",
           "LANG": "C.UTF-8"}
    env.update(extra_env or {})
    if SANDBOX_USER and shutil.which("sudo"):
        cmd = ["sudo", "-n", "-u", SANDBOX_USER, "env", "-i"] + \
              [f"{k}={v}" for k, v in env.items()] + argv
        run_env = None
    else:
        cmd, run_env = argv, env
    try:
        p = subprocess.run(cmd, cwd=cwd, env=run_env, capture_output=True,
                           text=True, timeout=timeout)
        return p.returncode, _trunc(p.stdout + p.stderr)
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        return 124, _trunc(out) + f"\nTIMEOUT after {timeout}s"


PYTEST_BASE = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
               "-o", "addopts=", "--no-header", "-rfE"]


def run_pytest(cwd: Path, targets: list[str] | None = None, k: str | None = None,
               timeout: int = 300, extra_env: dict | None = None) -> tuple[int, str]:
    argv = list(PYTEST_BASE)
    if k:
        argv += ["-k", k]
    argv += targets or ["tests"]
    env = {"PYTHONPATH": str(cwd)}  # import the tinydb of *this* tree (base or work), never site-packages
    env.update(extra_env or {})
    return sandbox_run(argv, cwd, timeout, env)


# ---------------------------------------------------------------- tool factories

def read_tools(jail: Jail) -> list[Tool]:
    def list_files(path: str = ".", pattern: str = "*") -> str:
        base = jail.resolve(path)
        if base.is_file():
            return base.relative_to(jail.root).as_posix()
        out = []
        for p in sorted(base.rglob(pattern)):
            try:
                jail.resolve(p.relative_to(jail.root).as_posix())
            except PermissionError:
                continue
            if p.is_file():
                out.append(p.relative_to(jail.root).as_posix())
            if len(out) >= 300:
                out.append("…(truncated)")
                break
        return "\n".join(out) or "(no files)"

    def read_file(path: str, start_line: int = 1, end_line: int = 0) -> str:
        p = jail.resolve(path)
        if not p.is_file():
            return f"ERROR: not a file: {path}"
        lines = p.read_text(errors="replace").splitlines()
        start = max(1, int(start_line))
        end = int(end_line) if end_line else min(len(lines), start + 399)
        end = min(end, len(lines), start + 399)
        body = "\n".join(f"{i:5d}| {lines[i - 1]}" for i in range(start, end + 1))
        more = f"\n… file has {len(lines)} lines; read more with start_line={end + 1}" if end < len(lines) else ""
        return body + more

    def search_code(pattern: str, path: str = ".") -> str:
        rx = re.compile(pattern)
        base = jail.resolve(path)
        hits = []
        files = [base] if base.is_file() else sorted(base.rglob("*"))
        for f in files:
            if not f.is_file() or f.suffix not in {".py", ".rst", ".md", ".txt", ".ini", ".toml", ".cfg"}:
                continue
            try:
                jail.resolve(f.relative_to(jail.root).as_posix())
            except PermissionError:
                continue
            for i, line in enumerate(f.read_text(errors="replace").splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{f.relative_to(jail.root).as_posix()}:{i}: {line.strip()[:200]}")
                    if len(hits) >= 80:
                        return "\n".join(hits) + "\n…(truncated at 80 hits)"
        return "\n".join(hits) or "(no matches)"

    return [
        Tool("list_files", "List files under a directory of the repository (recursive glob).",
             {"type": "object", "properties": {
                 "path": {"type": "string", "description": "directory relative to repo root"},
                 "pattern": {"type": "string", "description": "glob, e.g. '*.py'"}}}, list_files),
        Tool("read_file", "Read a text file with line numbers (max 400 lines per call).",
             {"type": "object", "properties": {
                 "path": {"type": "string"}, "start_line": {"type": "integer"},
                 "end_line": {"type": "integer"}}, "required": ["path"]}, read_file),
        Tool("search_code", "Regex search over source/docs files; returns file:line: text.",
             {"type": "object", "properties": {
                 "pattern": {"type": "string"}, "path": {"type": "string"}},
              "required": ["pattern"]}, search_code),
    ]


def write_tools(jail: Jail) -> list[Tool]:
    def write_file(path: str, content: str) -> str:
        p = jail.check_writable(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return f"wrote {p.relative_to(jail.root).as_posix()} ({len(content)} chars)"

    def replace_in_file(path: str, old: str, new: str) -> str:
        p = jail.check_writable(path)
        text = p.read_text()
        n = text.count(old)
        if n != 1:
            return f"ERROR: `old` must occur exactly once in {path}, found {n} times"
        p.write_text(text.replace(old, new, 1))
        return f"replaced 1 occurrence in {path}"

    return [
        Tool("write_file", "Create or overwrite a file (only allowed paths).",
             {"type": "object", "properties": {"path": {"type": "string"},
                                               "content": {"type": "string"}},
              "required": ["path", "content"]}, write_file),
        Tool("replace_in_file", "Replace one exact, unique occurrence of `old` with `new`.",
             {"type": "object", "properties": {"path": {"type": "string"},
                                               "old": {"type": "string"},
                                               "new": {"type": "string"}},
              "required": ["path", "old", "new"]}, replace_in_file),
    ]


def run_tools(jail: Jail, allow_python: bool = False, build: bool = False) -> list[Tool]:
    tools = []

    def run_tests(paths: list[str] | None = None, k: str = "") -> str:
        targets = []
        for t in paths or ["tests"]:
            node = t.split("::")[0]
            jail.resolve(node)  # jail check
            targets.append(t)
        code, out = run_pytest(jail.root, targets, k or None)
        return f"exit code {code}\n{out}"

    tools.append(Tool(
        "run_tests", "Run pytest on test paths (default: whole suite). Fixed command, no shell.",
        {"type": "object", "properties": {
            "paths": {"type": "array", "items": {"type": "string"}},
            "k": {"type": "string", "description": "optional -k expression"}}}, run_tests))

    if build:
        def check_build() -> str:
            code, out = sandbox_run([sys.executable, "-m", "compileall", "-q", "tinydb"], jail.root, 120,
                                    {"PYTHONPATH": str(jail.root)})
            if code:
                return f"compileall FAILED\n{out}"
            code, out = sandbox_run([sys.executable, "-c", "import tinydb; print('import ok')"],
                                    jail.root, 60, {"PYTHONPATH": str(jail.root)})
            return f"exit code {code}\n{out}"
        tools.append(Tool("check_build", "Byte-compile tinydb and import it.",
                          {"type": "object", "properties": {}}, check_build))

    if allow_python:
        def run_python(code: str) -> str:
            d = Path(tempfile.mkdtemp(prefix="autose-snippet-"))
            os.chmod(d, 0o777)
            f = d / "snippet.py"
            f.write_text(code)
            os.chmod(f, 0o644)
            rc, out = sandbox_run([sys.executable, str(f)], jail.root, 60,
                                  {"PYTHONPATH": str(jail.root)})
            return f"exit code {rc}\n{out}"
        tools.append(Tool("run_python", "Execute a Python snippet against the repository (60 s, sandbox).",
                          {"type": "object", "properties": {"code": {"type": "string"}},
                           "required": ["code"]}, run_python))
    return tools


def submit_tool(name: str, description: str, properties: dict, required: list[str],
                validate=None) -> Tool:
    def fn(**kwargs) -> str:
        missing = [r for r in required if not kwargs.get(r)]
        if missing:
            return f"ERROR: missing required fields {missing}"
        if validate:
            err = validate(kwargs)
            if err:
                return f"ERROR: {err}"
        return "submitted"
    return Tool(name, description, {"type": "object", "properties": properties,
                                    "required": required}, fn, terminal=True)
