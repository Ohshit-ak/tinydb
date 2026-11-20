"""Stage checks (ADR-007). Pure functions returning (passed, report_lines)."""
from __future__ import annotations

import ast
import fnmatch
import json
import re
import subprocess
import sys
from pathlib import Path

VAGUE = ["fast", "quick", "robust", "user-friendly", "user friendly", "seamless", "intuitive",
         "efficient", "properly", "correctly", "appropriate", "reasonable", "nice", "better",
         "etc.", "and/or", "as expected", "works well", "gracefully"]
ALLOWED_CHANGE = ["tinydb/*", "tinydb/**", "tests/*", "tests/**", "docs/*", "docs/**"]
FORBIDDEN_CHANGE = ["tests/conftest.py", "**/conftest.py", "conftest.py"]


def parse_acs(spec: str) -> dict[str, str]:
    """Return {AC-n: block text} from the '## Acceptance criteria' section."""
    m = re.search(r"^##\s+(?:\d+\.\s*)?Acceptance criteria.*?$(.*?)(?=^##\s|\Z)", spec, re.M | re.S | re.I)
    section = m.group(1) if m else ""
    blocks = re.split(r"^###\s+", section, flags=re.M)
    acs = {}
    for b in blocks:
        mm = re.match(r"(AC-\d+)\b", b.strip())
        if mm:
            acs[mm.group(1)] = b.strip()
    return acs


def spec_lint(spec: str) -> tuple[bool, list[str]]:
    acs = parse_acs(spec)
    rep = []
    if not acs:
        return False, ["no `### AC-n` entries found under `## Acceptance criteria`"]
    ok = True
    for ac, block in acs.items():
        low = block.lower()
        for kw in ("given", "when", "then"):
            if not re.search(rf"^\s*[-*]?\s*\**{kw}\b", block, re.I | re.M):
                ok = False
                rep.append(f"{ac}: missing a `{kw.capitalize()}` line")
        if "`" not in block:
            ok = False
            rep.append(f"{ac}: no code-level observable (no backticked call/value/exception)")
        bad = [w for w in VAGUE if re.search(rf"(?<![\w-]){re.escape(w)}(?![\w-])", low)]
        if bad:
            ok = False
            rep.append(f"{ac}: vague wording {bad}")
    if ok:
        rep.append(f"{len(acs)} acceptance criteria, all structurally testable")
    return ok, rep


def parse_judge(raw: str, acs: dict[str, str]) -> tuple[bool, list[str], dict]:
    try:
        data = json.loads(raw)
    except ValueError:
        m = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(m.group(0)) if m else {}
    rep, ok = [], True
    for ac in acs:
        v = data.get(ac)
        if not isinstance(v, dict) or v.get("testable") is not True:
            ok = False
            reason = v.get("reason") if isinstance(v, dict) else "no verdict"
            rep.append(f"{ac}: judged NOT testable — {reason}")
    if ok:
        rep.append("spec judge: every AC testable")
    return ok, rep, data


# ------------------------------------------------------------------ design

def _defined_names(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text())
    except (SyntaxError, OSError):
        return set()
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.ClassDef):
            names.add(node.name)
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    names.add(f"{node.name}.{sub.name}")
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
    return names


def parse_files_table(design: str) -> list[tuple[str, str, list[str]]]:
    m = re.search(r"^##\s+(?:\d+\.\s*)?Files to change.*?$(.*?)(?=^##\s|\Z)", design, re.M | re.S | re.I)
    rows = []
    if not m:
        return rows
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line.startswith("|") or re.match(r"^\|\s*:?-{2,}", line):
            continue
        cells = [c.strip().strip("`") for c in line.strip("|").split("|")]
        if len(cells) < 3 or cells[0].lower() in ("path", "file"):
            continue
        funcs = [f.strip().strip("`") for f in re.split(r"[,;]", cells[2]) if f.strip()]
        rows.append((cells[0], cells[1].lower(), funcs))
    return rows


def design_check(design: str, repo: Path, ac_ids: list[str]) -> tuple[bool, list[str]]:
    rows = parse_files_table(design)
    rep, ok = [], True
    if not rows:
        return False, ["no rows found in the `## Files to change` table"]
    for path, change, funcs in rows:
        is_new = "new" in change
        p = repo / path
        if not any(fnmatch.fnmatch(path, pat) for pat in ALLOWED_CHANGE):
            ok = False
            rep.append(f"{path}: outside the allowed roots (tinydb/, tests/, docs/)")
            continue
        if not is_new and not p.exists():
            ok = False
            rep.append(f"{path}: does not exist and is not marked NEW")
            continue
        if not funcs:
            ok = False
            rep.append(f"{path}: no functions/classes named")
            continue
        defined = _defined_names(p) if p.exists() and p.suffix == ".py" else set()
        for f in funcs:
            fname = re.sub(r"\(.*?\)", "", f).strip()
            if re.search(r"\bnew\b", f, re.I) or is_new or p.suffix != ".py":
                continue
            if fname not in defined:
                ok = False
                rep.append(f"{path}: `{fname}` not found by AST (mark it `(NEW)` if it is new)")
    mapping = re.search(r"^##\s+(?:\d+\.\s*)?AC mapping.*?$(.*?)(?=^##\s|\Z)", design, re.M | re.S | re.I)
    mtxt = mapping.group(1) if mapping else ""
    for ac in ac_ids:
        if not re.search(rf"\b{ac}\b", mtxt):
            ok = False
            rep.append(f"{ac}: not mapped in `## AC mapping`")
    if not re.search(r"^##\s+(?:\d+\.\s*)?Public interface", design, re.M | re.I):
        ok = False
        rep.append("missing `## Public interface` section (the Tester relies on it)")
    if ok:
        rep.append(f"{len(rows)} file(s) named; all functions resolved; all ACs mapped")
    return ok, rep


# ------------------------------------------------------------------ build/test

def git(repo: Path, *args: str, check: bool = True) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                          check=check).stdout


def changed_paths(repo: Path, base: str) -> list[str]:
    git(repo, "add", "-A", "--", "tinydb", "tests", "docs", check=False)
    out = git(repo, "diff", "--cached", "--name-only", base)
    return [l for l in out.splitlines() if l.strip()]


def path_policy(paths: list[str]) -> list[str]:
    bad = []
    for p in paths:
        if any(fnmatch.fnmatch(p, pat) for pat in FORBIDDEN_CHANGE) or \
           not any(fnmatch.fnmatch(p, pat) for pat in ALLOWED_CHANGE):
            bad.append(p)
    return bad


def test_names(test_file: Path) -> list[str]:
    try:
        tree = ast.parse(test_file.read_text())
    except (SyntaxError, OSError):
        return []
    return [n.name for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test_")]


def ac_coverage(names: list[str], ac_ids: list[str]) -> dict[str, list[str]]:
    cov = {}
    for ac in ac_ids:
        k = ac.split("-")[1]
        cov[ac] = [n for n in names if re.match(rf"test_ac{k}(_|$)", n)]
    return cov


def pytest_summary(out: str) -> str:
    lines = [l for l in out.splitlines() if re.search(r"\d+ (passed|failed|error)", l)]
    return lines[-1].strip() if lines else "(no summary line)"
