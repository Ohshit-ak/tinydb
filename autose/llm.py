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


_client: genai.Client | None = None


def client() -> genai.Client:
    global _client
    if _client is None:
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        _client = genai.Client(api_key=key)
    return _client


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


def _generate(model: str, contents: list, config: types.GenerateContentConfig):
    """generate_content with exponential backoff on 429/5xx (ADR-009)."""
    delay = 2.0
    for attempt in range(7):
        try:
            return client().models.generate_content(
                model=model, contents=contents, config=config)
        except genai_errors.APIError as e:  # ClientError / ServerError
            code = getattr(e, "code", None)
            if code in (429, 500, 502, 503, 504) and attempt < 6:
                time.sleep(delay + random.random())
                delay = min(delay * 2, 64)
                continue
            raise
    raise RuntimeError("unreachable")


def one_shot(model: str, system: str, prompt: str, usage: Usage, role: str,
             temperature: float = 0.0, json_mode: bool = False) -> str:
    cfg = types.GenerateContentConfig(
        system_instruction=system,
        temperature=temperature,
        response_mime_type="application/json" if json_mode else None,
    )
    resp = _generate(model, [types.Content(role="user", parts=[types.Part(text=prompt)])], cfg)
    usage.add(role, model, resp.usage_metadata)
    return resp.text or ""


def run_agent(*, role: str, model: str, system: str, task: str,
              tools: list[Tool], usage: Usage, max_steps: int,
              temperature: float = 0.2, log: Callable[[str], None] = print
              ) -> AgentResult:
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
    )
    contents: list = [types.Content(role="user", parts=[types.Part(text=task)])]
    result = AgentResult(submitted=None, steps=0)
    last_calls: list[str] = []

    for step in range(1, max_steps + 1):
        result.steps = step
        resp = _generate(model, contents, cfg)
        usage.add(role, model, resp.usage_metadata)
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
