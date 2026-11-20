"""Post-review job (ADR-002, ADR-008): no model key. Posts the review as a COMMENT
review (never APPROVE), labels the PR, and dispatches the next rework round
while the round budget allows it."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import gh

MAX_SENDBACKS = int(os.environ.get("AUTOSE_MAX_REVIEW_ROUNDS") or 2)
LABELS = ["autose:changes-requested", "autose:reviewer-approved", "autose:ready-for-human"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="review-out")
    ap.add_argument("--mode", default="full")
    a = ap.parse_args()
    v = json.loads((Path(a.dir) / "verdict.json").read_text())
    body = (Path(a.dir) / "review.md").read_text()
    pr, issue, rnd = v["pr"], v["issue"], v["round"]

    gh.call("POST", f"/repos/{gh.repo()}/pulls/{pr}/reviews",
            json={"commit_id": v["head"], "event": "COMMENT", "body": body + f"\n\nRun: {gh.run_url()}"})
    for l in LABELS:
        gh.remove_label(pr, l)

    if a.mode == "review-only":
        gh.add_labels(pr, ["autose:reviewer-approved" if v["verdict"] == "approve" else "autose:changes-requested"])
        return 0

    sendbacks_used = rnd - 1  # round 1 is the initial build; rounds 2..3 are rework rounds
    if v["verdict"] == "approve":
        gh.add_labels(pr, ["autose:reviewer-approved", "autose:ready-for-human"])
        msg, stage = f"Reviewer approved in round {rnd}. Ready for a human decision.", "done"
    elif sendbacks_used < MAX_SENDBACKS:
        gh.add_labels(pr, ["autose:changes-requested"])
        nxt = rnd + 1
        variant = os.environ.get("AUTOSE_VARIANT", "default")
        gh.call("POST", f"/repos/{gh.repo()}/actions/workflows/autose.yml/dispatches",
                json={"ref": os.environ.get("AUTOSE_DISPATCH_REF", "main"),
                      "inputs": {"mode": "rework", "issue": str(issue), "pr": str(pr), "round": str(nxt),
                                 "target": v["target"], "variant": variant,
                                 "prev_run": os.environ.get("GITHUB_RUN_ID", "")}})
        msg, stage = (f"Reviewer requested changes (target: **{v['target']}**). "
                      f"Dispatched rework round {nxt} of at most {MAX_SENDBACKS + 1}."), "rework"
    else:
        gh.add_labels(pr, ["autose:changes-requested", "autose:ready-for-human"])
        msg, stage = (f"Reviewer still requests changes after {MAX_SENDBACKS} rework rounds. "
                      "Stopping; a human must decide."), "done"
    gh.comment(issue, f"{gh.BOT_PREFIX} — review round {rnd}: `{v['verdict']}`. {msg}\nPR: #{pr} · Run: {gh.run_url()}")
    st, _ = gh.read_state(issue)
    st.update(round=rnd, stage=stage, last_verdict=v["verdict"], review_run=os.environ.get("GITHUB_RUN_ID", ""))
    gh.write_state(issue, st, msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
