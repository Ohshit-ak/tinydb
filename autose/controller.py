"""AutoSE Controller: deterministic state machine for stages 1-4 (ADR-002).

    python -m autose.controller --issue 3 --mode full --round 1
    python -m autose.controller --issue 3 --mode rework --round 2 --target implementation \
        --prev-dir .autose-run/prev --feedback-file .autose-run/prev/review.md

All pass/fail and back-edge decisions are made here, in code. Agents only
produce artifacts. Every transition is appended to state.json["history"].
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import checks
from .llm import AgentStuck, BudgetExceeded, Usage, check_models, one_shot, run_agent
from .tools import Jail, read_tools, run_pytest, run_tools, submit_tool, write_tools

PKG = Path(__file__).resolve().parent
PROMPTS = PKG / "prompts"

WORKER = os.environ.get("AUTOSE_WORKER_MODEL") or "gemini-2.5-flash"
TOKEN_CAP = int(os.environ.get("AUTOSE_TOKEN_CAP") or 2_000_000)
STEPS = {"analyst": 15, "architect": 20, "builder": 40, "tester": 25}


def prompt(name: str, **subs: str) -> str:
    text = (PROMPTS / f"{name}.md").read_text()
    for k, v in subs.items():
        text = text.replace("{{" + k + "}}", v)
    return text


def fence(text: str, tag: str = "untrusted_issue") -> str:
    nonce = secrets.token_hex(4)
    text = text.replace(f"</{tag}", f"<\\/{tag}")
    return f'<{tag} nonce="{nonce}">\n{text}\n</{tag} nonce="{nonce}">'


class Stop(Exception):
    def __init__(self, stage: str, reason: str):
        super().__init__(reason)
        self.stage, self.reason = stage, reason


class Controller:
    def __init__(self, args):
        self.a = args
        self.repo = Path(args.repo).resolve()
        self.run = Path(args.run_dir).resolve()
        self.run.mkdir(parents=True, exist_ok=True)
        (self.run / "agent_logs").mkdir(exist_ok=True)
        self.usage = Usage(self.run / "usage.jsonl", TOKEN_CAP)
        self.n = args.issue
        self.test_file = f"tests/test_autose_issue_{self.n}.py"
        self.single_pass = args.variant == "single-pass"
        self.max_builder = 1 if self.single_pass else 3
        self.state = {"issue": self.n, "round": args.round, "mode": args.mode,
                      "target": args.target, "variant": args.variant,
                      "worker_model": WORKER, "status": "running",
                      "history": [], "stages": {}, "started": time.time()}
        self.issue = self._load_issue()
        self.base = args.base or checks.git(self.repo, "rev-parse", "HEAD").strip()
        self.state["base"] = self.base

    # ------------------------------------------------------------ utilities
    def log(self, msg: str) -> None:
        print(msg, flush=True)

    def note(self, frm: str, to: str, why: str) -> None:
        self.state["history"].append({"t": round(time.time() - self.state["started"], 1),
                                      "from": frm, "to": to, "why": why})
        self.log(f"==> {frm} -> {to}: {why}")
        self.save()

    def save(self) -> None:
        self.state["tokens_total"] = self.usage.total
        (self.run / "state.json").write_text(json.dumps(self.state, indent=2))

    def write(self, name: str, text: str) -> None:
        (self.run / name).write_text(text)

    def read(self, name: str) -> str:
        p = self.run / name
        if p.exists():
            return p.read_text()
        prev = Path(self.a.prev_dir) / name if self.a.prev_dir else None
        if prev and prev.exists():
            return prev.read_text()
        return ""

    def dump_log(self, role: str, attempt: int, res) -> None:
        (self.run / "agent_logs" / f"{role}-{attempt}.json").write_text(json.dumps(
            {"role": role, "steps": res.steps, "stop_reason": res.stop_reason,
             "tool_log": res.tool_log}, indent=2))

    def _load_issue(self) -> dict:
        if self.a.issue_file:
            return json.loads(Path(self.a.issue_file).read_text())
        from . import gh
        d = gh.get_issue(self.n)
        return {"number": self.n, "title": d["title"], "body": d.get("body") or ""}

    # ------------------------------------------------------------ stage 1
    def stage_spec(self) -> None:
        skill = (PKG / "skills/requirements-spec/SKILL.md").read_text()
        system = prompt("analyst", SKILL=skill)
        feedback = ""
        for attempt in (1, 2):
            task = (f"Write the specification for GitHub issue #{self.n}.\n\n"
                    f"Issue title (untrusted): {self.issue['title']!r}\n\n"
                    f"{fence(self.issue['body'])}\n")
            if feedback:
                task += f"\n\nYour previous spec failed the stage check:\n{feedback}\nFix every point."
            res = run_agent(role="analyst", model=WORKER, system=system, task=task,
                            tools=read_tools(Jail(self.repo)) + [submit_tool(
                                "submit", "Submit the finished spec.md.",
                                {"spec_md": {"type": "string"}}, ["spec_md"])],
                            usage=self.usage, max_steps=STEPS["analyst"])
            self.dump_log("analyst", attempt, res)
            spec = (res.submitted or {}).get("spec_md", "")
            if not spec:
                feedback = f"no spec submitted ({res.stop_reason})"
                self.note("SPEC", "SPEC" if attempt == 1 else "STOPPED", feedback)
                continue
            ok_lint, rep_lint = checks.spec_lint(spec)
            acs = checks.parse_acs(spec)
            ok_judge, rep_judge, judge = (False, ["skipped: lint failed"], {})
            if ok_lint:
                raw = one_shot(WORKER, prompt("spec_judge"),
                               "Acceptance criteria to judge:\n\n" +
                               "\n\n".join(f"### {b}" for b in acs.values()),
                               self.usage, "spec_judge", json_mode=True)
                ok_judge, rep_judge, judge = checks.parse_judge(raw, acs)
            report = "\n".join(f"- {l}" for l in rep_lint + rep_judge)
            spec_out = spec + ("\n\n---\n## Appendix — stage 1 check (Controller)\n"
                               f"Result: {'PASS' if ok_lint and ok_judge else 'FAIL'}\n{report}\n\n"
                               f"Spec judge raw verdicts:\n```json\n{json.dumps(judge, indent=2)}\n```\n")
            self.write("spec.md", spec_out)
            self.state["stages"]["spec"] = {"attempt": attempt, "pass": ok_lint and ok_judge,
                                            "acs": list(acs)}
            if ok_lint and ok_judge:
                self.note("SPEC_CHECK", "DESIGN", f"{len(acs)} ACs, all testable")
                return
            feedback = report
            self.note("SPEC_CHECK", "SPEC" if attempt == 1 else "STOPPED", "spec check failed")
        raise Stop("spec", f"Spec did not pass its check after 2 attempts:\n{feedback}")

    def ac_ids(self) -> list[str]:
        return list(checks.parse_acs(self.read("spec.md")))

    # ------------------------------------------------------------ stage 2
    def stage_design(self, feedback: str = "") -> None:
        arch = (self.repo / "ARCHITECTURE.md").read_text() if (self.repo / "ARCHITECTURE.md").exists() else "(none)"
        check_fb = ""
        for attempt in (1, 2):
            task = (f"Issue #{self.n} title (untrusted): {self.issue['title']!r}\n"
                    f"The new test file will be `{self.test_file}`.\n\n"
                    f"<spec_md>\n{self.read('spec.md')}\n</spec_md>\n\n"
                    f"<architecture_md>\n{arch}\n</architecture_md>\n")
            if self.read("design.md") and (feedback or check_fb):
                task += f"\n<previous_design_md>\n{self.read('design.md')}\n</previous_design_md>\n"
            if feedback:
                task += f"\n<feedback>\n{feedback}\n</feedback>\nRevise the design to address this feedback.\n"
            if check_fb:
                task += f"\nYour previous design failed the stage check:\n{check_fb}\nFix every point.\n"
            res = run_agent(role="architect", model=WORKER, system=prompt("architect"), task=task,
                            tools=read_tools(Jail(self.repo)) + [submit_tool(
                                "submit", "Submit the finished design.md.",
                                {"design_md": {"type": "string"}}, ["design_md"])],
                            usage=self.usage, max_steps=STEPS["architect"])
            self.dump_log("architect", len(list((self.run / "agent_logs").glob("architect-*"))) + 1, res)
            design = (res.submitted or {}).get("design_md", "")
            if not design:
                check_fb = f"no design submitted ({res.stop_reason})"
                self.note("DESIGN", "DESIGN" if attempt == 1 else "STOPPED", check_fb)
                continue
            ok, rep = checks.design_check(design, self.repo, self.ac_ids())
            report = "\n".join(f"- {l}" for l in rep)
            self.write("design.md", design + f"\n\n---\n## Appendix — stage 2 check (Controller)\n"
                                              f"Result: {'PASS' if ok else 'FAIL'}\n{report}\n")
            self.state["stages"]["design"] = {"attempt": attempt, "pass": ok}
            if ok:
                self.note("DESIGN_CHECK", "BUILD", "files and functions resolved, ACs mapped")
                return
            check_fb = report
            self.note("DESIGN_CHECK", "DESIGN" if attempt == 1 else "STOPPED", "design check failed")
        raise Stop("design", f"Design did not pass its check after 2 attempts:\n{check_fb}")

    # ------------------------------------------------------------ stage 3
    def builder(self, attempt: int, feedback: str) -> str:
        jail = Jail(self.repo, writable=["tinydb/*", "tinydb/**", "docs/*", "docs/**"])
        task = (f"Implement the change for issue #{self.n}.\n\n<spec_md>\n{self.read('spec.md')}\n</spec_md>\n\n"
                f"<design_md>\n{self.read('design.md')}\n</design_md>\n")
        if feedback:
            task += f"\n<feedback attempt=\"{attempt}\">\n{feedback}\n</feedback>\nAddress this feedback.\n"
        res = run_agent(role="builder", model=WORKER, system=prompt("builder"), task=task,
                        tools=read_tools(jail) + write_tools(jail) + run_tools(jail, build=True) +
                        [submit_tool("submit", "Finish implementation.", {"summary": {"type": "string"}},
                                     ["summary"])],
                        usage=self.usage, max_steps=STEPS["builder"])
        self.dump_log("builder", attempt, res)
        return (res.submitted or {}).get("summary", f"(no submit: {res.stop_reason})")

    def build_check(self) -> tuple[bool, str]:
        paths = checks.changed_paths(self.repo, self.base)
        code_paths = [p for p in paths if p != self.test_file]
        bad = checks.path_policy(paths)
        if bad:
            # revert disallowed edits rather than shipping them
            for p in bad:
                subprocess.run(["git", "checkout", self.base, "--", p], cwd=self.repo, capture_output=True)
            return False, f"changes outside the allowlist were reverted: {bad}"
        if not code_paths:
            return False, "no code changes (empty diff in tinydb/ or docs/)"
        p = subprocess.run([sys.executable, "-m", "compileall", "-q", "tinydb"], cwd=self.repo,
                           capture_output=True, text=True)
        if p.returncode:
            return False, f"compileall failed:\n{p.stdout[-3000:]}{p.stderr[-3000:]}"
        p = subprocess.run([sys.executable, "-c", "import tinydb"], cwd=self.repo,
                           capture_output=True, text=True)
        if p.returncode:
            return False, f"import tinydb failed:\n{p.stderr[-3000:]}"
        return True, f"build ok; changed: {code_paths}"

    def write_diff(self) -> None:
        checks.git(self.repo, "add", "-A", "--", "tinydb", "tests", "docs", check=False)
        self.write("diff.patch", checks.git(self.repo, "diff", "--cached", self.base))

    # ------------------------------------------------------------ stage 4
    def base_tree(self) -> Path:
        wt = self.run / "base"
        if not wt.exists():
            checks.git(self.repo, "worktree", "add", "--detach", str(wt), self.base)
        return wt

    def tester(self, attempt: int, feedback: str) -> str:
        base = self.base_tree()
        work_jail = Jail(self.repo, writable=[self.test_file])
        base_jail = Jail(base)
        test_file = self.test_file

        def run_tests_on_base(paths=None, k: str = "") -> str:
            # The Tester only ever runs its tests against the OLD code (ADR-003).
            src = self.repo / test_file
            if src.exists():
                (base / test_file).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(src, base / test_file)
            code, out = run_pytest(base, [test_file] if not paths else paths, k or None)
            return f"(ran against the BASE commit, i.e. the old code)\nexit code {code}\n{out}"

        from .llm import Tool
        tools = read_tools(base_jail) + write_tools(work_jail)[:1] + [
            Tool("run_tests", "Run pytest against the old (base) code. Failures of new-behaviour tests are expected.",
                 {"type": "object", "properties": {"paths": {"type": "array", "items": {"type": "string"}},
                                                   "k": {"type": "string"}}}, run_tests_on_base),
            submit_tool("submit", "Finish writing tests.", {"summary": {"type": "string"}}, ["summary"])]
        spec = self.read("spec.md")
        acs = checks.parse_acs(spec)
        design = self.read("design.md")
        m = re.search(r"^##\s+(?:\d+\.\s*)?Public interface.*?$(.*?)(?=^##\s|\Z)", design, re.M | re.S | re.I)
        iface = m.group(1).strip() if m else "(not specified)"
        task = (f"Write `{test_file}` for issue #{self.n}.\n\n<acceptance_criteria>\n" +
                "\n\n".join(f"### {b}" for b in acs.values()) +
                f"\n</acceptance_criteria>\n\n<public_interface>\n{iface}\n</public_interface>\n")
        if feedback:
            task += f"\n<feedback>\n{feedback}\n</feedback>\n"
        res = run_agent(role="tester", model=WORKER, system=prompt("tester", TEST_FILE=test_file),
                        task=task, tools=tools, usage=self.usage, max_steps=STEPS["tester"])
        self.dump_log("tester", attempt, res)
        return (res.submitted or {}).get("summary", f"(no submit: {res.stop_reason})")

    def test_check(self) -> tuple[bool, dict, str]:
        tf = self.repo / self.test_file
        names = checks.test_names(tf) if tf.exists() else []
        cov = checks.ac_coverage(names, self.ac_ids())
        missing = [ac for ac, t in cov.items() if not t]
        new_code, new_out = run_pytest(self.repo, [self.test_file, "-rA"]) if tf.exists() else (1, "test file missing")
        full_code, full_out = run_pytest(self.repo, ["tests"])
        # red-on-base (information only, ADR-007)
        base = self.base_tree()
        red = {}
        if tf.exists():
            (base / self.test_file).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(tf, base / self.test_file)
            _, bout = run_pytest(base, [self.test_file, "-rA"])
            for mm in re.finditer(r"^(PASSED|FAILED|ERROR) \S+::(\w+)", bout, re.M):
                red[mm.group(2)] = mm.group(1)
        res = {"coverage": cov, "missing": missing, "new_exit": new_code, "full_exit": full_code,
               "new_summary": checks.pytest_summary(new_out), "full_summary": checks.pytest_summary(full_out),
               "red_on_base": red}
        ok = not missing and new_code == 0 and full_code == 0
        fail_out = ""
        if new_code:
            fail_out += f"New tests ({self.test_file}) failed:\n{new_out[-5000:]}\n"
        if full_code:
            fail_out += f"Full suite failed:\n{full_out[-5000:]}\n"
        return ok, res, fail_out

    def write_test_report(self, attempts: list[dict], final: dict | None, stopped: str = "") -> None:
        L = [f"# Test report — issue #{self.n} (round {self.a.round}, variant {self.a.variant})", ""]
        L += ["| Attempt | Builder summary (truncated) | Build | New tests | Full suite | Result |",
              "|---|---|---|---|---|---|"]
        for a in attempts:
            L.append(f"| {a['n']} | {a.get('summary', '')[:120].replace('|', '/').replace(chr(10), ' ')} | "
                     f"{'ok' if a.get('build') else 'FAIL'} | {a.get('new', '-')} | {a.get('full', '-')} | "
                     f"{'PASS' if a.get('pass') else 'FAIL'} |")
        if final:
            L += ["", "## Acceptance-criterion coverage", "", "| AC | Tests | Red on base? |", "|---|---|---|"]
            for ac, ts in final["coverage"].items():
                reds = ", ".join(f"{t}: {final['red_on_base'].get(t, '?')}" for t in ts) or "—"
                L.append(f"| {ac} | {', '.join(ts) or '**MISSING**'} | {reds} |")
            L += ["", f"- New tests: `{final['new_summary']}`", f"- Full suite: `{final['full_summary']}`",
                  "- *Red on base* = the new test FAILED against the unmodified code (the expected result for "
                  "new behaviour; tests of unchanged behaviour pass on base by design)."]
        L += ["", f"## Check result: {'STOPPED — ' + stopped if stopped else 'PASS'}"]
        self.write("test_report.md", "\n".join(L) + "\n")

    # ------------------------------------------------------------ stages 3+4 loop
    def build_and_test(self, feedback: str, need_tests: bool, tests_feedback: str = "") -> None:
        attempts, redesigned = [], False
        tester_runs = 0
        res: dict | None = None
        for n in range(1, self.max_builder + 1):
            rec = {"n": n}
            attempts.append(rec)
            if feedback is not None:
                rec["summary"] = self.builder(n, feedback)
            ok, msg = self.build_check()
            rec["build"] = ok
            self.write_diff()
            if not ok:
                self.note("BUILD_CHECK", "BUILD", msg[:300])
                feedback = f"Build check failed:\n{msg}"
                continue
            self.note("BUILD_CHECK", "TEST", msg[:300])
            if need_tests or not (self.repo / self.test_file).exists():
                tester_runs += 1
                self.tester(tester_runs, tests_feedback)
                need_tests = False
            final_ok, res, fail_out = self.test_check()
            if res["missing"] and tester_runs < 2 and not self.single_pass:
                self.note("TEST_CHECK", "TEST", f"ACs without tests: {res['missing']}")
                tester_runs += 1
                self.tester(tester_runs, f"Missing tests for: {res['missing']}. Name them test_ac<k>_...")
                final_ok, res, fail_out = self.test_check()
            rec.update(new=res["new_summary"], full=res["full_summary"], **{"pass": final_ok})
            self.write_diff()
            self.state["stages"]["test"] = {"attempt": n, "pass": final_ok, **{k: res[k] for k in ("missing", "new_summary", "full_summary")}}
            if final_ok:
                self.write_test_report(attempts, res)
                self.note("TEST_CHECK", "DONE", "all new and existing tests pass")
                return
            feedback = fail_out or f"Missing AC coverage: {res['missing']}"
            if n == 2 and not redesigned and not self.single_pass:
                redesigned = True
                self.write_test_report(attempts, res)
                self.note("TEST_CHECK", "DESIGN", "tests failed twice; redesign")
                self.stage_design(feedback=f"Implementation attempts failed these tests:\n{fail_out[-4000:]}")
            else:
                self.note("TEST_CHECK", "BUILD", "tests failed; back to implementation")
            self.write_test_report(attempts, res)
        self.write_test_report(attempts, res,
                               stopped=f"tests still failing after {self.max_builder} implementation attempt(s)")
        raise Stop("test", f"tests still failing after {self.max_builder} attempt(s)")

    # ------------------------------------------------------------ main
    def commit(self) -> None:
        branch = f"autose/issue-{self.n}" + ("" if self.a.variant == "default" else f"-{self.a.variant}")
        checks.git(self.repo, "add", "-A", "--", "tinydb", "tests", "docs", check=False)
        env = dict(os.environ, GIT_AUTHOR_NAME="autose-bot", GIT_AUTHOR_EMAIL="autose-bot@users.noreply.github.com",
                   GIT_COMMITTER_NAME="autose-bot", GIT_COMMITTER_EMAIL="autose-bot@users.noreply.github.com")
        subprocess.run(["git", "commit", "-q", "-m", f"autose: changes for #{self.n} (round {self.a.round})"],
                       cwd=self.repo, env=env, check=True)
        checks.git(self.repo, "branch", "-f", branch, "HEAD")
        subprocess.run(["git", "bundle", "create", str(self.run / "branch.bundle"), f"{self.base}..{branch}"],
                       cwd=self.repo, check=True, capture_output=True)
        self.state["branch"] = branch
        self.state["head"] = checks.git(self.repo, "rev-parse", "HEAD").strip()

    def main(self) -> int:
        try:
            if not self.a.skip_model_check:
                check_models(WORKER)
            mode, target = self.a.mode, self.a.target
            fb = Path(self.a.feedback_file).read_text() if self.a.feedback_file else ""
            if mode == "full":
                self.note("START", "SPEC", f"issue #{self.n}, round {self.a.round}")
                self.stage_spec()
                self.stage_design()
                self.build_and_test(feedback="", need_tests=True)
            else:  # rework after review (ADR-002): re-enter at the reviewer's target stage
                for name in ("spec.md", "design.md", "test_report.md"):
                    if not (self.run / name).exists() and self.read(name):
                        self.write(name, self.read(name))
                self.note("START", target.upper(), f"rework round {self.a.round} from review")
                review_fb = f"Independent review requested changes:\n{fb}"
                if target == "design":
                    self.stage_design(feedback=review_fb)
                    self.build_and_test(feedback=review_fb, need_tests=True)
                elif target == "tests":
                    self.build_and_test(feedback=None, need_tests=True, tests_feedback=review_fb)
                else:
                    self.build_and_test(feedback=review_fb, need_tests=False)
            self.commit()
            self.state["status"] = "done"
        except Stop as s:
            self.state.update(status="stopped", stopped_stage=s.stage, stopped_reason=s.reason)
            art = {"spec": "spec.md", "design": "design.md", "test": "test_report.md"}[s.stage]
            with (self.run / art).open("a") as f:
                f.write(f"\n\n## STOPPED\nThe Controller stopped the run at this stage.\n\nReason:\n{s.reason}\n")
            self.note(s.stage.upper(), "STOPPED", s.reason[:300])
        except (BudgetExceeded, AgentStuck) as e:
            self.state.update(status="stopped", stopped_stage="budget", stopped_reason=str(e))
            self.write("STOPPED.md", f"# STOPPED\n\n{e}\n")
            self.note("ANY", "STOPPED", str(e))
        finally:
            self.save()
            try:
                checks.git(self.repo, "worktree", "remove", "--force", str(self.run / "base"), check=False)
            except Exception:
                pass
        from .gh import gh_output
        gh_output(status=self.state["status"], branch=self.state.get("branch", ""))
        return 0


def parse_args(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--issue", type=int, required=True)
    ap.add_argument("--mode", choices=["full", "rework"], default="full")
    ap.add_argument("--round", type=int, default=1)
    ap.add_argument("--target", choices=["design", "implementation", "tests"], default="implementation")
    ap.add_argument("--variant", default="default", choices=["default", "single-pass"])
    ap.add_argument("--repo", default=".")
    ap.add_argument("--run-dir", default=".autose-run")
    ap.add_argument("--base", default="")
    ap.add_argument("--prev-dir", default="")
    ap.add_argument("--feedback-file", default="")
    ap.add_argument("--issue-file", default="", help="local dry run: JSON with number/title/body")
    ap.add_argument("--skip-model-check", action="store_true")
    return ap.parse_args(argv)


if __name__ == "__main__":
    sys.exit(Controller(parse_args()).main())
