"""Provider-neutral planners for OpenAI-compatible chat APIs.

The provider only produces a declarative harness document. It never receives
or executes the Unreal transport.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional
from urllib.request import Request, urlopen


class ProviderError(RuntimeError):
    """Raised when a model response is unavailable or not a safe plan."""


class OpenAICompatiblePlanner:
    """Planner for OpenAI-compatible endpoints, including Ollama and vLLM."""

    def __init__(
        self,
        endpoint: Optional[str] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = 120.0,
    ) -> None:
        self.endpoint = (endpoint or os.environ.get("UNREAL_HARNESS_LLM_ENDPOINT") or "http://127.0.0.1:11434/v1").rstrip("/")
        self.model = model or os.environ.get("UNREAL_HARNESS_LLM_MODEL")
        self.api_key = api_key if api_key is not None else os.environ.get("UNREAL_HARNESS_LLM_API_KEY")
        self.timeout = timeout
        if not self.model:
            raise ProviderError("LLM model is required (--model or UNREAL_HARNESS_LLM_MODEL)")

    def __call__(self, task: str, context: Dict[str, Any]) -> Dict[str, Any]:
        catalog = context.get("capabilities", {}).get("actions", [])
        system = (
            "You are a planner for a safe Unreal Engine 5.8 JSON harness. "
            "Return one JSON object only with format_version and commands. "
            "Use only actions from the supplied catalog. Never return Python, shell, markdown, or prose. "
            "Inspect before mutation when needed; include project.save for persistence."
        )
        user = json.dumps({"task": task, "capabilities": context.get("capabilities", {}), "allowed_actions": catalog}, ensure_ascii=False)
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_object"},
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        request = Request(self.endpoint + "/chat/completions", data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        try:
            with urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise ProviderError("LLM request failed: {}".format(exc)) from exc
        try:
            content = body["choices"][0]["message"]["content"]
            plan = json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ProviderError("LLM returned no valid JSON plan") from exc
        self.validate_plan(plan, catalog)
        return plan

    @staticmethod
    def validate_plan(plan: Dict[str, Any], catalog: list[str]) -> None:
        if not isinstance(plan, dict) or not isinstance(plan.get("commands"), list):
            raise ProviderError("LLM plan must be an object with commands")
        if len(plan["commands"]) > 200:
            raise ProviderError("LLM plan exceeds 200 commands")
        seen: set[str] = set()
        allowed = set(catalog)
        for command in plan["commands"]:
            if not isinstance(command, dict) or not isinstance(command.get("id"), str) or not isinstance(command.get("action"), str):
                raise ProviderError("LLM plan contains an invalid command")
            if command["id"] in seen:
                raise ProviderError("LLM plan contains duplicate command ids")
            dependencies = command.get("depends_on", [])
            if not isinstance(dependencies, list) or any(dep not in seen for dep in dependencies):
                raise ProviderError("LLM plan contains an invalid dependency")
            if allowed and command["action"] not in allowed:
                raise ProviderError("LLM selected unsupported action: {}".format(command["action"]))
            seen.add(command["id"])
