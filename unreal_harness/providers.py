"""Provider-neutral planners for OpenAI-compatible chat APIs."""
from __future__ import annotations
import json
import os
from typing import Any, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from .action_contract import ActionValidationError, validate_document

class ProviderError(RuntimeError):
    """Raised when a model response is unavailable or not a safe plan."""

class OpenAICompatiblePlanner:
    MAX_CORRECTIONS = 2
    def __init__(self, endpoint: Optional[str] = None, model: Optional[str] = None, api_key: Optional[str] = None, timeout: float = 120.0, max_corrections: int = MAX_CORRECTIONS) -> None:
        self.endpoint = (endpoint or os.environ.get("UNREAL_HARNESS_LLM_ENDPOINT") or "http://127.0.0.1:11434/v1").rstrip("/")
        self.model = model or os.environ.get("UNREAL_HARNESS_LLM_MODEL")
        self.api_key = api_key if api_key is not None else os.environ.get("UNREAL_HARNESS_LLM_API_KEY")
        self.timeout = timeout
        self.max_corrections = max(0, min(int(max_corrections), 5))
        if not self.model:
            raise ProviderError("LLM model is required (--model or UNREAL_HARNESS_LLM_MODEL)")

    def __call__(self, task: str, context: Dict[str, Any]) -> Dict[str, Any]:
        capabilities = context.get("capabilities", {})
        raw_actions = capabilities.get("actions", []) if isinstance(capabilities, dict) else []
        catalog = [a if isinstance(a, str) else a.get("name", a.get("action", "")) for a in raw_actions if isinstance(a, (str, dict))]
        catalog = [a for a in catalog if a]
        metadata = raw_actions if len(json.dumps(raw_actions, ensure_ascii=False)) <= 48000 else catalog
        system = "You are a planner for a safe Unreal Engine 5.8 JSON harness. Return one JSON object only. Use only supplied actions and documented arguments. Never return Python, shell, markdown, or prose. Never save for a read-only task; include project.save only when persistence is requested."
        user = json.dumps({"task": task, "capabilities": capabilities, "action_catalog": metadata, "allowed_actions": catalog}, ensure_ascii=False)
        payload = {"model": self.model, "temperature": 0, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}], "response_format": {"type": "json_object"}}
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        last_error = ""
        for attempt in range(self.max_corrections + 1):
            current = payload if attempt == 0 else self._correction_payload(payload, task, last_error)
            plan = self._parse_plan(self._request_safe(current, headers, catalog))
            try:
                validate_document(plan)
                self.validate_plan(plan, catalog)
                return plan
            except (ActionValidationError, ProviderError) as exc:
                last_error = str(exc)
                if attempt >= self.max_corrections:
                    raise ProviderError("LLM plan validation failed after {} corrections: {}".format(self.max_corrections, last_error)) from exc
        raise ProviderError("LLM plan validation failed")

    @staticmethod
    def _correction_payload(payload: Dict[str, Any], task: str, error: str) -> Dict[str, Any]:
        corrected = dict(payload)
        corrected["messages"] = list(payload["messages"]) + [{"role": "user", "content": json.dumps({"correction": "Return a corrected JSON plan for the original task. Do not add scope.", "original_task": task, "validation_error": error}, ensure_ascii=False)}]
        return corrected

    def _request_safe(self, payload: Dict[str, Any], headers: Dict[str, str], catalog: Optional[list[str]] = None) -> Dict[str, Any]:
        try:
            return self._request(payload, headers)
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            code = exc.code
            exc.close()
            if code == 400 and "response_format" in detail and "response_format" in payload:
                retry = dict(payload)
                retry["response_format"] = {"type": "json_schema", "json_schema": {"name": "unreal_plan", "schema": {"type": "object", "properties": {"format_version": {"type": "string"}, "commands": {"type": "array", "items": {"type": "object", "properties": {"id": {"type": "string"}, "action": {"type": "string", "enum": list(catalog or [])}, "arguments": {"type": "object"}, "depends_on": {"type": "array", "items": {"type": "string"}}}, "required": ["id", "action", "arguments"]}}}, "required": ["format_version", "commands"]}}}
                try:
                    return self._request(retry, headers)
                except HTTPError as retry_error:
                    body = retry_error.read().decode("utf-8", errors="replace")
                    retry_error.close()
                    raise ProviderError("LLM request failed: HTTP {}: {}".format(retry_error.code, body)) from retry_error
                except Exception as retry_error:
                    raise ProviderError("LLM request failed: {}".format(retry_error)) from retry_error
            raise ProviderError("LLM request failed: HTTP {}: {}".format(code, detail)) from exc
        except (URLError, OSError, ValueError) as exc:
            raise ProviderError("LLM request failed: {}".format(exc)) from exc

    @staticmethod
    def _parse_plan(body: Dict[str, Any]) -> Dict[str, Any]:
        try:
            content = body["choices"][0]["message"]["content"]
            if isinstance(content, list):
                content = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
            if isinstance(content, dict):
                return content
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                raise TypeError("plan must be object")
            return parsed
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ProviderError("LLM returned malformed output: expected JSON object in choices[0].message.content") from exc

    @staticmethod
    def validate_plan(plan: Dict[str, Any], catalog: list[str]) -> None:
        if not isinstance(plan, dict) or not isinstance(plan.get("commands"), list):
            raise ProviderError("LLM plan must be an object with commands")
        if len(plan["commands"]) > 200:
            raise ProviderError("LLM plan exceeds 200 commands")
        allowed, seen = set(catalog), set()
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

    def _request(self, payload: Dict[str, Any], headers: Dict[str, str]) -> Dict[str, Any]:
        request = Request(self.endpoint + "/chat/completions", data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        with urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))
