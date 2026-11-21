"""Minimal Gemini function-calling agent loop (ADR-005, ADR-006, ADR-009).

One `run_agent` call is one isolated agent session: its own system prompt,
its own tool set, its own message history. Nothing is shared between sessions
except the artifacts the Controller passes in explicitly (ADR-003).
"""
from __future__ import annotations

import json
import os
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from google import genai
from google.genai import errors as genai_errors
from google.genai import types


class BudgetExceeded(RuntimeError):
    pass


class AgentStuck(RuntimeError):
    pass


class QuotaExhausted(RuntimeError):
    """A daily/plan quota (not a transient rate limit): retrying cannot help (ADR-009)."""
    pass


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict  # JSON schema
    fn: Callable[..., str]
    terminal: bool = False  # calling it ends the session (submit tools)


@dataclass
class AgentResult:
    submitted: dict | None  # args of the terminal tool, or None
    steps: int
    tool_log: list[dict] = field(default_factory=list)
    stop_reason: str = ""
    final_text: str = ""


class Usage:
    """Token accounting across the whole run, persisted to usage.jsonl."""

    def __init__(self, path: Path, cap: int):
        self.path = path
        self.cap = cap
        self.total = 0
        if path.exists():
            for line in path.read_text().splitlines():
                try:
                    self.total += int(json.loads(line).get("total", 0))
                except ValueError:
                    pass

    def add(self, role: str, model: str, um: Any) -> None:
        rec = {
            "ts": time.time(),
            "role": role,
            "model": model,
            "prompt": getattr(um, "prompt_token_count", 0) or 0,
            "output": getattr(um, "candidates_token_count", 0) or 0,
            "thoughts": getattr(um, "thoughts_token_count", 0) or 0,
            "total": getattr(um, "total_token_count", 0) or 0,
        }
        self.total += rec["total"]
        with self.path.open("a") as f:
            f.write(json.dumps(rec) + "\n")
        if self.total > self.cap:
            raise BudgetExceeded(
                f"token budget exceeded: {self.total} > {self.cap}")


_clients: list[genai.Client] = []
_key_idx = 0  # index of the key currently in use


def _keys() -> list[str]:
    keys = [k.strip() for k in (os.environ.get("GEMINI_API_KEYS") or "").split(",") if k.strip()]
    if not keys and os.environ.get("GEMINI_API_KEY"):
        keys = [os.environ["GEMINI_API_KEY"]]
    if not keys:
        raise RuntimeError("no Gemini key: set GEMINI_API_KEYS (or GEMINI_API_KEY)")
    return keys


def client() -> genai.Client:
    if not _clients:
        _clients.extend(genai.Client(api_key=k, http_options=types.HttpOptions(timeout=90_000))  # ms; a hung request becomes a retry
                        for k in _keys())
    return _clients[_key_idx]


def check_models(*model_ids: str) -> None:
    """Fail fast with a clear message if a configured model id is unknown."""
    available = set()
    for m in client().models.list():
        available.add(m.name.removeprefix("models/"))
    missing = [m for m in model_ids if m not in available]
    if missing:
        gem = sorted(a for a in available if a.startswith("gemini"))
        raise RuntimeError(
            f"configured model(s) {missing} not available for this key; "
            f"available gemini models: {gem}")


# ADR-011 (amended): every free-tier model has its own 20 requests/day. A role starts on its
# configured model and moves to the next model of the *same* pool when that model's daily quota
# is spent (or it stays unavailable). Worker and reviewer pools are disjoint (ADR-008).
POOLS = [
    [m.strip() for m in (os.environ.get("AUTOSE_WORKER_POOL") or
     "gemini-3.5-flash-lite,gemini-3.5-flash,gemini-3.6-flash").split(",") if m.strip()],
    [m.strip() for m in (os.environ.get("AUTOSE_REVIEWER_POOL") or
     "gemini-3.1-flash-lite,gemini-3.7-flash,gemini-3.8-flash,gemini-4-hydrogen").split(",") if m.strip()],
]
_exhausted: set[tuple[int, str]] = set()  # (key index, model)


def _candidates(model: str) -> list[str]:
    for pool in POOLS:
        if model in pool:
            i = pool.index(model)
            return [m for m in pool[i:] + pool[:i] if (_key_idx, m) not in _exhausted]
    return [model] if (_key_idx, model) not in _exhausted else []


def _strip_signatures(contents: list) -> None:
    """History produced by another model: use Gemini's documented bypass value for thought
    signatures so the next model accepts the transcript."""
    for c in contents:
        for p in (getattr(c, "parts", None) or []):
            if getattr(p, "thought_signature", None):
                p.thought_signature = b"skip_thought_signature_validator"


def _generate(model: str, contents: list, config: types.GenerateContentConfig):
    """generate_content with backoff on transient errors and pool fallback on exhausted
    quota (ADR-009/011). Returns (response, model actually used)."""
    global _key_idx
    client()  # initialise the key list
    tried = []
    while True:
        r = _try_pool(model, contents, config, tried)
        if r is not None:
            return r
        if _key_idx + 1 < len(_clients):  # every model of this key is spent: next key
            _key_idx += 1
            print(f"[llm] all models exhausted on key #{_key_idx}; switching to key #{_key_idx + 1}", flush=True)
            continue
        raise QuotaExhausted(f"all models in the pool of {model} are exhausted/unavailable on all keys today (tried {tried})")


def _try_pool(model: str, contents: list, config, tried: list):
    for m in _candidates(model):
        if tried:
            _strip_signatures(contents)
            print(f"[llm] falling back from {tried[-1]} to {m}", flush=True)
        tried.append(m)
        delay = 2.0
        for attempt in range(5):
            try:
                return client().models.generate_content(model=m, contents=contents, config=config), m
            except genai_errors.APIError as e:  # ClientError / ServerError
                code = getattr(e, "code", None)
                msg = str(e)
                if code == 429 and ("PerDay" in msg or "free_tier" in msg.lower()):
                    _exhausted.add((_key_idx, m))
                    break
                if code in (429, 500, 502, 503, 504) and attempt < 4:
                    time.sleep(delay + random.random())
                    delay = min(delay * 2, 32)
                    continue
                if code in (500, 502, 503, 504):  # still unavailable: try the next model
                    break
                raise
            except Exception as e:  # transport errors (connection reset, server disconnected, timeouts)
                if attempt < 4:
                    print(f"[llm] transport error on {m}: {type(e).__name__}; retrying", flush=True)
                    time.sleep(delay + random.random())
                    delay = min(delay * 2, 32)
                    continue
                break
    return None


def _thinking(level: str | None):
    # "" / None = model default; otherwise "low" | "medium" | "high" (ADR-011)
    return types.ThinkingConfig(thinking_level=level) if level else None


def one_shot(model: str, system: str, prompt: str, usage: Usage, role: str,
             temperature: float = 0.0, json_mode: bool = False, thinking: str | None = None) -> str:
    cfg = types.GenerateContentConfig(
        system_instruction=system,
        temperature=temperature,
        response_mime_type="application/json" if json_mode else None,
        thinking_config=_thinking(thinking),
    )
    resp, used = _generate(model, [types.Content(role="user", parts=[types.Part(text=prompt)])], cfg)
    usage.add(role, used, resp.usage_metadata)
    return resp.text or ""


def run_agent(*, role: str, model: str, system: str, task: str,
              tools: list[Tool], usage: Usage, max_steps: int,
              temperature: float = 0.2, log: Callable[[str], None] = print,
              thinking: str | None = None) -> AgentResult:
    """Run one ReAct-style tool loop until a terminal tool is called."""
    decls = [types.FunctionDeclaration(name=t.name, description=t.description,
                                       parameters_json_schema=t.parameters)
             for t in tools]
    by_name = {t.name: t for t in tools}
    cfg = types.GenerateContentConfig(
        system_instruction=system,
        temperature=temperature,
        tools=[types.Tool(function_declarations=decls)],
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(mode="ANY")),
        thinking_config=_thinking(thinking),
    )
    contents: list = [types.Content(role="user", parts=[types.Part(text=task)])]
    result = AgentResult(submitted=None, steps=0)
    last_calls: list[str] = []

    for step in range(1, max_steps + 1):
        result.steps = step
        if step == max_steps - 1 and len(contents) > 1:
            # budget nearly spent: ask for the deliverable instead of more exploration (ADR-009)
            contents.append(types.Content(role="user", parts=[types.Part(
                text="Step budget almost exhausted: call the submit tool NOW with your best complete result.")]))
        resp, used = _generate(model, contents, cfg)
        usage.add(role, used, resp.usage_metadata)
        cand = resp.candidates[0] if resp.candidates else None
        if cand is None or cand.content is None:
            result.stop_reason = f"empty response (finish_reason={getattr(cand, 'finish_reason', None)})"
            return result
        contents.append(cand.content)
        calls = resp.function_calls or []
        if not calls:
            # mode=ANY should prevent this; nudge once per step.
            result.final_text = resp.text or ""
            contents.append(types.Content(role="user", parts=[types.Part(
                text="You must respond with a tool call. Call `submit` when done.")]))
            continue

        responses = []
        for call in calls:
            args = dict(call.args or {})
            sig = call.name + json.dumps(args, sort_keys=True)[:2000]
            last_calls.append(sig)
            if len(last_calls) >= 3 and len(set(last_calls[-3:])) == 1:
                raise AgentStuck(f"{role}: same tool call repeated 3 times: {call.name}")
            tool = by_name.get(call.name)
            if tool is None:
                out = f"ERROR: unknown tool {call.name}"
            else:
                try:
                    out = tool.fn(**args)
                except TypeError as e:
                    out = f"ERROR: bad arguments: {e}"
                except Exception as e:  # tool errors are fed back to the model
                    out = f"ERROR: {type(e).__name__}: {e}"
            entry = {"step": step, "tool": call.name,
                     "args": {k: (v if len(str(v)) < 300 else str(v)[:300] + "…") for k, v in args.items()},
                     "result_head": str(out)[:400]}
            result.tool_log.append(entry)
            log(f"[{role}] step {step}: {call.name}({', '.join(f'{k}=' + repr(v)[:60] for k, v in args.items())})")
            if tool is not None and tool.terminal and not str(out).startswith("ERROR"):
                result.submitted = args
                result.stop_reason = "submitted"
                return result
            responses.append(types.Part.from_function_response(
                name=call.name, response={"result": str(out)}))
        contents.append(types.Content(role="user", parts=responses))

    result.stop_reason = f"step budget exhausted ({max_steps})"
    return result
