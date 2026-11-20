"""Release job, stage 5 (ADR-005): deterministic, no model key.

Fetches the git bundle produced by the agents job, re-checks the path policy,
pushes the branch, opens or updates the PR, writes pr.md and the state comment.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from . import checks, gh


def sh(*a: str, cwd: Path) -> str:
    return subprocess.run(list(a), cwd=cwd, capture_output=True, text=True, check=True).stdout


def section(md: str, heading: str) -> str:
    m = re.search(rf"^##\s+[\d. ]*{heading}.*?$(.*?)(?=^##\s|\Z)", md, re.M | re.S | re.I)
    return m.group(1).strip() if m else ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", default=".autose-run")
    ap.add_argument("--repo", default=".")
    a = ap.parse_args()
    run, repo = Path(a.run_dir), Path(a.repo)
    st = json.loads((run / "state.json").read_text())
    n, branch, base = st["issue"], st["branch"], st["base"]
    issue = gh.get_issue(n)

    # 1. bring in the branch from the bundle and re-check the path policy (ADR-009 §5)
    sh("git", "fetch", "-q", str(run / "branch.bundle"), f"{branch}:{branch}", cwd=repo)
    paths = [p for p in sh("git", "diff", "--name-only", f"{base}..{branch}", cwd=repo).splitlines() if p]
    bad = checks.path_policy(paths)
    if bad:
        msg = f"Release refused: the change touches paths outside the allowlist: {bad}"
        (run / "pr.md").write_text(f"# Release stopped\n\n{msg}\n")
        gh.comment(n, f"{gh.BOT_PREFIX} — **stage 5 stopped.** {msg}\nRun: {gh.run_url()}")
        gh.add_labels(n, ["autose:failed"])
        gh.gh_output(status="stopped", pr="", head="")
        return 0
    sh("git", "push", "-q", "--force", "origin", f"{branch}:refs/heads/{branch}", cwd=repo)
    head = sh("git", "rev-parse", branch, cwd=repo).strip()

    # 2. pr.md (for humans; the Reviewer never receives it, ADR-008)
    spec = (run / "spec.md").read_text()
    design = (run / "design.md").read_text()
    report = (run / "test_report.md").read_text()
    acs = checks.parse_acs(spec)
    tokens = st.get("tokens_total", 0)
    body = [f"Resolves #{n}", "",
            f"> {gh.BOT_PREFIX} opened this PR automatically (round {st['round']}, variant `{st['variant']}`). "
            "A person decides whether to merge it.", "",
            "## Acceptance criteria (from spec.md)", ""]
    body += [f"- **{ac}** — {blk.splitlines()[0].split(':', 1)[-1].strip()}" for ac, blk in acs.items()]
    body += ["", "## Design summary", "", section(design, "Summary")[:1500] or "(see design.md)", "",
             "## Files changed", ""] + [f"- `{p}`" for p in paths]
    body += ["", "## Test report", "", section(report, "Acceptance-criterion coverage")[:3000], "",
             "## Trace", "",
             f"- Actions run (artifacts: spec.md, design.md, diff.patch, test_report.md, state.json): {gh.run_url()}",
             f"- Base `{base[:7]}` → head `{head[:7]}`; worker model `{st.get('worker_model')}`; "
             f"tokens this run: {tokens:,}",
             "- State transitions: " + " → ".join(h["to"] for h in st["history"])]
    pr_md = "\n".join(body)
    title = f"AutoSE: {issue['title']} (#{n})"
    if st["variant"] != "default":
        title += f" [{st['variant']}]"
    existing = gh.find_open_pr(branch)
    if existing:
        pr = gh.call("PATCH", f"/repos/{gh.repo()}/pulls/{existing['number']}", json={"body": pr_md})
    else:
        default = gh.call("GET", f"/repos/{gh.repo()}")["default_branch"]
        pr = gh.call("POST", f"/repos/{gh.repo()}/pulls",
                     json={"title": title, "head": branch, "base": default, "body": pr_md})
    pr_n = pr["number"]
    pr = gh.call("GET", f"/repos/{gh.repo()}/pulls/{pr_n}")
    if pr["state"] != "open":
        gh.gh_output(status="stopped", pr=str(pr_n), head=head)
        return 0
    gh.add_labels(pr_n, ["autose"])
    (run / "pr.md").write_text(f"# PR #{pr_n}: {pr['html_url']}\n\n{pr_md}\n")

    # 3. human-readable issue comment + machine state (ADR-004)
    gh.comment(n, f"{gh.BOT_PREFIX} — stages 1–4 passed, PR opened: {pr['html_url']}\n\n"
                  f"<details><summary>Stage results</summary>\n\n"
                  f"| Stage | Result |\n|---|---|\n"
                  f"| 1 Requirements | {len(acs)} ACs, all testable |\n"
                  f"| 2 Design | {len(checks.parse_files_table(design))} file(s) planned |\n"
                  f"| 3 Implementation | builds |\n"
                  f"| 4 Testing | {st['stages'].get('test', {}).get('full_summary', '')} |\n"
                  f"| 5 Release | PR #{pr_n} open; CI running |\n\n</details>\n\nRun: {gh.run_url()}")
    gh.write_state(n, {"issue": n, "round": st["round"], "branch": branch, "base": base, "head": head,
                       "pr": pr_n, "run_id": gh.run_url().rsplit("/", 1)[-1], "variant": st["variant"],
                       "stage": "review"},
                   f"Round {st['round']}: PR #{pr_n}, head `{head[:7]}`, waiting for CI and review.")
    gh.gh_output(status="released", pr=str(pr_n), head=head)
    return 0


if __name__ == "__main__":
    sys.exit(main())
