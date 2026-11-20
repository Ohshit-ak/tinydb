"""Gate job (ADR-010): decide whether and how to run. No model key, no checkout of code.

Reads the event payload from GITHUB_EVENT_PATH (never via ${{ }} interpolation
into shell), checks the sender's repository permission, and emits outputs.
"""
from __future__ import annotations

import json
import os
import re
import sys

from . import gh

ALLOWED = {"admin", "maintain", "write"}
LABEL = os.environ.get("AUTOSE_LABEL", "autose")


def decide(event_name: str, ev: dict) -> dict:
    out = {"proceed": "false", "mode": "", "issue": "", "pr": "", "round": "1",
           "target": "implementation", "variant": "default", "reviewer_variant": "independent",
           "prev_run": "", "reason": ""}
    sender = ev.get("sender", {}) or {}
    if sender.get("type") == "Bot" and event_name != "workflow_dispatch":
        out["reason"] = "bot sender ignored"
        return out

    if event_name == "issues":
        if (ev.get("label") or {}).get("name") != LABEL:
            out["reason"] = "label is not the trigger label"
            return out
        if ev["issue"].get("pull_request"):
            out["reason"] = "label on a PR ignored"
            return out
        out.update(mode="full", issue=str(ev["issue"]["number"]))
    elif event_name == "issue_comment":
        body = (ev["comment"].get("body") or "").strip()
        first = body.splitlines()[0].strip() if body else ""
        if not first.startswith("/autose"):
            out["reason"] = "not an /autose command"
            return out
        if ev["comment"].get("author_association") not in ("OWNER", "MEMBER", "COLLABORATOR"):
            out["reason"] = "comment author is not a collaborator"
            return out
        parts = first.split()
        if ev["issue"].get("pull_request"):
            if len(parts) < 2 or parts[1] != "review":
                out["reason"] = "on a PR only `/autose review [coupled]` is supported"
                return out
            pr = ev["issue"]["number"]
            pr_body = ev["issue"].get("body") or ""
            m = re.search(r"(?:fixes|closes|resolves)\s+#(\d+)", pr_body, re.I)
            out.update(mode="review-only", pr=str(pr), issue=m.group(1) if m else "",
                       reviewer_variant="coupled" if "coupled" in parts[2:] else "independent")
            if not out["issue"]:
                out["reason"] = "PR body does not reference an issue (Fixes #n)"
                return out
        else:
            out.update(mode="full", issue=str(ev["issue"]["number"]))
            if "single-pass" in parts[1:]:
                out["variant"] = "single-pass"
    elif event_name == "workflow_dispatch":
        i = ev.get("inputs", {}) or {}
        out.update(mode=i.get("mode", "full"), issue=str(i.get("issue", "")), pr=str(i.get("pr", "") or ""),
                   round=str(i.get("round", "1") or "1"), target=i.get("target", "implementation"),
                   variant=i.get("variant", "default"),
                   reviewer_variant=i.get("reviewer_variant", "independent"),
                   prev_run=str(i.get("prev_run", "") or ""))
    else:
        out["reason"] = f"unsupported event {event_name}"
        return out

    # Who may start a run: write/maintain/admin only (triage is not enough).
    if event_name != "workflow_dispatch":  # dispatch already requires write access
        perm = gh.sender_permission(sender.get("login", ""))
        if perm not in ALLOWED:
            out["reason"] = f"sender permission '{perm}' is not allowed"
            return out
    if not out["issue"].isdigit():
        out["reason"] = "no issue number"
        return out
    out["proceed"] = "true"
    return out


def main() -> int:
    ev = json.load(open(os.environ["GITHUB_EVENT_PATH"]))
    out = decide(os.environ["GITHUB_EVENT_NAME"], ev)
    gh.gh_output(**out)
    if out["proceed"] == "true" and out["mode"] in ("full", "review-only"):
        n = int(out["pr"] or out["issue"])
        what = {"full": "issue → PR pipeline", "review-only": "independent review"}[out["mode"]]
        extra = f" (variant `{out['variant']}`)" if out["variant"] != "default" else ""
        extra += f" (reviewer `{out['reviewer_variant']}`)" if out["reviewer_variant"] != "independent" else ""
        gh.comment(n, f"{gh.BOT_PREFIX} started the {what}{extra}: {gh.run_url()}")
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
