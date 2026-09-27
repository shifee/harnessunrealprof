"""Provider-neutral planners for OpenAI-compatible chat APIs.

The provider only produces a declarative harness document. It never receives
or executes the Unreal transport.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional
from urllib.error import HTTPError
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
            "Use only documented argument names. Never invent fields such as filter, objects, or reference. "
            "For a request to find or list actors on the current level, use one level.inspect command. "
            "Its arguments may contain class (full Unreal class path), query, selected_only, and limit. "
            "For PlayerStart use class '/Script/Engine.PlayerStart'. "
            "level.inspect already returns each actor's label, class, path, and location coordinates; "
            "do not add object.describe or object.inspect for those fields. "
            "Inspect before mutation when needed. Include project.save only after a mutating command "
            "when persistence is requested. Never save for a read-only task."
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
        try:
            body = self._request(payload, headers)
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            exc.close()
            if exc.code == 400 and "response_format" in detail:
                payload = dict(payload)
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "unreal_plan",
                        "schema": {
                            "type": "object",
                            "properties": {
                                "format_version": {"type": "string", "enum": ["1.0"]},
                                "commands": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "id": {"type": "string"},
                                            "action": {"type": "string", "enum": catalog},
                                            "arguments": {"type": "object"},
                                            "depends_on": {"type": "array", "items": {"type": "string"}},
                                        },
                                        "required": ["id", "action", "arguments"],
                                    },
                                },
                            },
                            "required": ["format_version", "commands"],
                        },
                    },
                }
                try:
                    body = self._request(payload, headers)
                except HTTPError as retry_error:
                    retry_detail = retry_error.read().decode("utf-8", errors="replace")
                    retry_error.close()
                    raise ProviderError("LLM request failed: HTTP {}: {}".format(retry_error.code, retry_detail)) from retry_error
                except Exception as retry_error:
                    raise ProviderError("LLM request failed: {}".format(retry_error)) from retry_error
            else:
                raise ProviderError("LLM request failed: HTTP {}: {}".format(exc.code, detail)) from exc
        except Exception as exc:
            raise ProviderError("LLM request failed: {}".format(exc)) from exc
        try:
            content = body["choices"][0]["message"]["content"]
            plan = json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ProviderError("LLM returned no valid JSON plan") from exc
        self.validate_plan(plan, catalog)
        return plan

    def _request(self, payload: Dict[str, Any], headers: Dict[str, str]) -> Dict[str, Any]:
        request = Request(
            self.endpoint + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

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
