"""Tiny GitHub REST helper used only by the deterministic jobs (gate, release, post-review)
and for *reading* issues/PRs in agent jobs. Agents never get these functions as tools."""
from __future__ import annotations

import json
import os
import re
import time

import requests

API = os.environ.get("GITHUB_API_URL", "https://api.github.com")
STATE_MARK = "<!-- autose-state"
BOT_PREFIX = "🤖 AutoSE"


def repo() -> str:
    return os.environ["GITHUB_REPOSITORY"]


def _h() -> dict:
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    tok = os.environ.get("GITHUB_TOKEN")
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def call(method: str, path: str, **kw):
    url = path if path.startswith("http") else f"{API}{path}"
    for attempt in range(4):
        r = requests.request(method, url, headers=_h(), timeout=30, **kw)
        if r.status_code in (502, 503, 504) or (r.status_code == 403 and "rate limit" in r.text.lower()):
            time.sleep(2 ** attempt * 3)
            continue
        break
    if r.status_code >= 400:
        raise RuntimeError(f"GitHub {method} {path} -> {r.status_code}: {r.text[:500]}")
    return r.json() if r.text.strip() else {}


def get_issue(n: int) -> dict:
    return call("GET", f"/repos/{repo()}/issues/{n}")


def comment(n: int, body: str) -> dict:
    return call("POST", f"/repos/{repo()}/issues/{n}/comments", json={"body": body})


def add_labels(n: int, labels: list[str]) -> None:
    call("POST", f"/repos/{repo()}/issues/{n}/labels", json={"labels": labels})


def remove_label(n: int, label: str) -> None:
    try:
        call("DELETE", f"/repos/{repo()}/issues/{n}/labels/{requests.utils.quote(label)}")
    except RuntimeError:
        pass


def sender_permission(user: str) -> str:
    d = call("GET", f"/repos/{repo()}/collaborators/{user}/permission")
    return d.get("permission", "none")


def read_state(issue: int) -> tuple[dict, int | None]:
    """Return (state, comment_id) from the issue's autose-state comment."""
    page = 1
    found, cid = {}, None
    while True:
        cs = call("GET", f"/repos/{repo()}/issues/{issue}/comments?per_page=100&page={page}")
        for c in cs:
            if c.get("user", {}).get("type") == "Bot" and STATE_MARK in (c.get("body") or ""):
                m = re.search(r"<!-- autose-state (\{.*?\}) -->", c["body"], re.S)
                if m:
                    found, cid = json.loads(m.group(1)), c["id"]
        if len(cs) < 100:
            return found, cid
        page += 1


def write_state(issue: int, state: dict, human: str) -> None:
    _, cid = read_state(issue)
    body = f"{BOT_PREFIX} — run state\n\n{human}\n\n<!-- autose-state {json.dumps(state)} -->"
    if cid:
        call("PATCH", f"/repos/{repo()}/issues/comments/{cid}", json={"body": body})
    else:
        comment(issue, body)


def find_open_pr(branch: str) -> dict | None:
    owner = repo().split("/")[0]
    prs = call("GET", f"/repos/{repo()}/pulls?state=open&head={owner}:{branch}")
    return prs[0] if prs else None


def gh_output(**kv) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    lines = [f"{k}={v}" for k, v in kv.items()]
    if path:
        with open(path, "a") as f:
            f.write("\n".join(lines) + "\n")
    print("\n".join(f"output {l}" for l in lines))


def run_url() -> str:
    s = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    return f"{s}/{repo()}/actions/runs/{os.environ.get('GITHUB_RUN_ID', '0')}"
