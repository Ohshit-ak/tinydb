"""Review job, stage 6 (ADR-008). Runs on a fresh runner with a read-only token.

Inputs are deliberately limited to: the issue text, `git diff base...head`, and
the repository checked out at the PR head. The `coupled` variant exists only for
decision experiment 1 and is never the default.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import gh
from .controller import fence, prompt
from .llm import Usage, check_models, run_agent
from .tools import Jail, read_tools, run_tools, submit_tool

REVIEWER = os.environ.get("AUTOSE_REVIEWER_MODEL") or "gemini-2.5-pro"
WORKER = os.environ.get("AUTOSE_WORKER_MODEL") or "gemini-2.5-flash"

REVIEW_SCHEMA = {
    "requirements": {"type": "array", "items": {"type": "object", "properties": {
        "id": {"type": "string"}, "text": {"type": "string"},
        "status": {"type": "string", "enum": ["met", "not met", "unclear"]},
        "evidence": {"type": "string"}}, "required": ["id", "text", "status", "evidence"]}},
    "findings": {"type": "array", "items": {"type": "object", "properties": {
        "severity": {"type": "string", "enum": ["blocker", "major", "minor"]},
        "target": {"type": "string", "enum": ["design", "implementation", "tests"]},
        "file": {"type": "string"}, "line": {"type": "integer"},
        "problem": {"type": "string"}, "suggestion": {"type": "string"}},
        "required": ["severity", "target", "problem"]}},
    "verdict": {"type": "string", "enum": ["approve", "request_changes"]},
    "target": {"type": "string", "enum": ["design", "implementation", "tests", "none"]},
    "summary": {"type": "string"},
}


def render(v: dict, meta: dict) -> str:
    L = [f"## {gh.BOT_PREFIX} independent review — round {meta['round']}",
         "", f"**Verdict: `{v['verdict']}`**" + (f" → route to **{v.get('target')}**" if v["verdict"] != "approve" else ""),
         "", v.get("summary", ""), "", "### Requirements derived from the issue", "",
         "| ID | Requirement | Status | Evidence |", "|---|---|---|---|"]
    for r in v.get("requirements", []):
        L.append(f"| {r.get('id')} | {r.get('text', '').replace('|', '/')} | {r.get('status')} | "
                 f"{r.get('evidence', '').replace('|', '/').replace(chr(10), ' ')[:400]} |")
    L += ["", "### Findings", ""]
    if not v.get("findings"):
        L.append("None.")
    for f in v.get("findings", []):
        loc = f"`{f.get('file')}:{f.get('line')}`" if f.get("file") else ""
        L.append(f"- **{f.get('severity')}** ({f.get('target')}) {loc} — {f.get('problem')}"
                 + (f"\n  - Suggestion: {f.get('suggestion')}" if f.get("suggestion") else ""))
    L += ["", "<details><summary>Reviewer evidence log (tools the reviewer actually ran)</summary>", ""]
    for t in meta["tool_log"]:
        if t["tool"] in ("run_tests", "run_python"):
            L.append(f"- step {t['step']} `{t['tool']}` → `{t['result_head'][:160].replace(chr(10), ' ')}`")
    L += ["", "</details>", "",
          f"_Model `{meta['model']}`, variant `{meta['variant']}`, {meta['steps']} steps. "
          f"Inputs: issue text, diff, repo at `{meta['head'][:7]}`. The reviewer could not push or see the "
          f"implementer's artifacts. A person decides whether to merge._"]
    if meta.get("guard"):
        L.insert(3, f"> Controller guard: {meta['guard']}\n")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pr", type=int, required=True)
    ap.add_argument("--issue", type=int, required=True)
    ap.add_argument("--round", type=int, default=1)
    ap.add_argument("--variant", default="independent", choices=["independent", "coupled"])
    ap.add_argument("--out", default="review-out")
    ap.add_argument("--repo", default=".")
    a = ap.parse_args()
    repo, out = Path(a.repo).resolve(), Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(repo / ".autose-run", ignore_errors=True)  # defence in depth (ADR-004)

    pr = gh.call("GET", f"/repos/{gh.repo()}/pulls/{a.pr}")
    issue = gh.get_issue(a.issue)
    base_sha, head_sha = pr["base"]["sha"], pr["head"]["sha"]
    diff = subprocess.run(["git", "diff", f"{base_sha}...{head_sha}"], cwd=repo,
                          capture_output=True, text=True, check=True).stdout
    model = REVIEWER if a.variant == "independent" else WORKER
    check_models(model)
    usage = Usage(out / "usage.jsonl", int(os.environ.get("AUTOSE_TOKEN_CAP") or 2_000_000))

    task = (f"Review pull request #{a.pr}, which claims to address issue #{a.issue}.\n\n"
            f"Issue title (untrusted): {issue['title']!r}\n{fence(issue.get('body') or '')}\n\n"
            f"<diff base=\"{base_sha[:7]}\" head=\"{head_sha[:7]}\">\n{diff[:60000]}\n</diff>\n")
    system = prompt("reviewer")
    if a.variant == "coupled":  # experiment only: sees the author's description, friendly prompt, same model
        system = prompt("reviewer_coupled")
        task += f"\n<author_description>\n{pr.get('body') or ''}\n</author_description>\n"

    jail = Jail(repo)
    tools = read_tools(jail) + run_tools(jail, allow_python=True) + [
        submit_tool("submit_review", "Submit the review verdict.", REVIEW_SCHEMA,
                    ["requirements", "verdict", "summary"])]
    res = run_agent(role="reviewer", model=model, system=system, task=task, tools=tools,
                    usage=usage, max_steps=30, temperature=0.2)
    v = res.submitted or {"verdict": "request_changes", "target": "implementation", "requirements": [],
                          "findings": [], "summary": f"Reviewer did not submit ({res.stop_reason})."}
    v.setdefault("findings", [])

    # Evidence guard (ADR-007): approval needs a real test run in the harness's own log.
    guard = ""
    ran_tests = any(t["tool"] == "run_tests" for t in res.tool_log)
    if v["verdict"] == "approve" and (not ran_tests or not v.get("requirements")):
        guard = "approval downgraded to request_changes — no `run_tests` call or no requirements checked."
        v["verdict"] = "request_changes"
        v["target"] = v.get("target") if v.get("target") not in (None, "none") else "tests"
    if v["verdict"] == "request_changes" and v.get("target") in (None, "none", ""):
        sev = [f for f in v["findings"] if f.get("severity") in ("blocker", "major")] or v["findings"]
        v["target"] = sev[0]["target"] if sev else "implementation"
    meta = {"round": a.round, "model": model, "variant": a.variant, "steps": res.steps,
            "head": head_sha, "tool_log": res.tool_log, "guard": guard}
    (out / "review.md").write_text(render(v, meta))
    (out / "verdict.json").write_text(json.dumps({**v, "guard": guard, "ran_tests": ran_tests,
                                                  "model": model, "variant": a.variant,
                                                  "head": head_sha, "pr": a.pr, "issue": a.issue,
                                                  "round": a.round, "tokens": usage.total}, indent=2))
    (out / "reviewer_tool_log.json").write_text(json.dumps(res.tool_log, indent=2))
    gh.gh_output(verdict=v["verdict"], target=v.get("target") or "implementation")
    print(f"verdict={v['verdict']} target={v.get('target')} guard={guard!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
