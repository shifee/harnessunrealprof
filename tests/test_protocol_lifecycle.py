import io
import json
import tempfile
import threading
import unittest
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import URLError
from unittest.mock import patch

from test_task_agent import World, RESEARCH, CREATE, FINISH, CRITERIA
from unreal_harness.providers import OpenAICompatiblePlanner, ProviderError
from unreal_harness.task_agent import TaskAgent
from unreal_harness.task_store import load_task


class ProtocolLifecycleTests(unittest.TestCase):
    def test_real_http_chat_protocol_preserves_utf8_observations_and_usage(self):
        requests = []
        outputs = deque([RESEARCH, {"status": "ready", "goal": "create requested folder", "criteria": CRITERIA}, CREATE, FINISH])
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append((self.path, self.headers.get("Authorization"), payload))
                response = {"choices": [{"message": {"content": json.dumps(outputs.popleft(), ensure_ascii=False)}}],
                            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}}
                encoded = json.dumps(response).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
            def log_message(self, *args): pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as root:
                planner = OpenAICompatiblePlanner(endpoint="http://127.0.0.1:{}/v1".format(server.server_port), model="fixture", api_key="test-secret")
                result = TaskAgent(World(root), planner).run("Создай папку /Game/Target")
                self.assertTrue(result["success"], result)
                state = load_task(root, result["task_id"])
                responses = [e for e in state["events"] if e["kind"] == "model_response"]
                self.assertEqual(len(responses), 4)
                self.assertTrue(all(e["response"]["usage"]["total_tokens"] == 120 for e in responses))
                self.assertNotIn("test-secret", Path(result["audit_path"]).read_text(encoding="utf-8"))
            self.assertEqual(len(requests), 4)
            self.assertTrue(all(path == "/v1/chat/completions" and auth == "Bearer test-secret" for path, auth, _ in requests))
            latest_context = json.loads(requests[-1][2]["messages"][-1]["content"])
            self.assertEqual(latest_context["task"], "Создай папку /Game/Target")
            self.assertEqual(latest_context["history"][-1]["result"]["changed_objects"], ["/Game/Target"])
        finally:
            server.shutdown()
            thread.join()
            server.server_close()

    def test_model_timeout_and_invalid_port_rejected_before_network(self):
        for value in (0, -1, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                OpenAICompatiblePlanner(model="fixture", timeout=value)
        with self.assertRaises(ProviderError):
            OpenAICompatiblePlanner(model="fixture", endpoint="http://localhost:invalid")

    def test_network_failure_has_trace_event(self):
        planner = OpenAICompatiblePlanner(model="fixture")
        events = []
        planner.trace_callback = events.append
        with patch.object(planner, "_request", side_effect=URLError("offline")), self.assertRaises(ProviderError):
            planner._request_safe({"model": "fixture", "messages": []}, {})
        self.assertEqual([e["kind"] for e in events], ["model_request", "model_error"])

    def test_unexpected_planner_failure_still_finalizes_checkpoint(self):
        with tempfile.TemporaryDirectory() as root:
            planner = OpenAICompatiblePlanner(model="fixture")
            with patch.object(planner, "next_step", side_effect=IndexError("broken adapter")):
                result = TaskAgent(World(root), planner).run("inspect")
            self.assertFalse(result["success"])
            self.assertEqual(result["exception_type"], "IndexError")
            self.assertEqual(load_task(root, result["task_id"])["status"], "stopped")
