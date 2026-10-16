import io
import json
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from scripts import install
from unreal_harness.cli import main, make_parser, _human_summary
from unreal_harness.health import diagnose
from unreal_harness.providers import list_models, ProviderError


class HealthTests(unittest.TestCase):
    def install_project(self, root):
        project = root / "Test.uproject"
        project.write_text('{"EngineAssociation":"5.8"}', encoding="utf-8")
        with redirect_stdout(io.StringIO()):
            install.install(install.parse_args(["--project", str(project), "--enable-plugins"]))
        return project

    def test_offline_diagnostics_never_contact_editor_or_model(self):
        with tempfile.TemporaryDirectory() as directory:
            project = self.install_project(Path(directory))
            def forbidden(*args, **kwargs):
                self.fail("offline diagnostics contacted a service")
            report = diagnose(project, offline=True, transport_factory=forbidden, models_loader=forbidden)
            self.assertTrue(report["success"], report)
            self.assertFalse((project.parent / "Content/Python/harness.lock").exists())

    def test_runtime_drift_blocks_editor_probe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = self.install_project(root)
            runtime = root / "Content/Python/init_unreal.py"
            runtime.write_text("# old watcher\n", encoding="utf-8")
            def forbidden(*args, **kwargs):
                self.fail("editor probe despite stale runtime")
            report = diagnose(project, model="test", transport_factory=forbidden, models_loader=lambda **kwargs: ["test"])
            self.assertFalse(report["success"])
            failed = next(c for c in report["checks"] if c["name"] == "init_unreal.py")
            self.assertIn("runtime-only", failed["fix"])

    def test_live_checks_expose_wrong_model_and_missing_graph_bridge(self):
        class Transport:
            def __init__(self, *args, **kwargs): pass
            def submit(self, document):
                self_document = document["commands"]
                assert len(self_document) == 1 and self_document[0]["action"] == "system.capabilities"
                return {"success": True, "commands": [{"data": {"engine_version": "5.8", "graph_bridge_available": False}}]}
        with tempfile.TemporaryDirectory() as directory:
            project = self.install_project(Path(directory))
            report = diagnose(project, model="wrong", transport_factory=Transport, models_loader=lambda **kwargs: ["local-model"])
            self.assertFalse(report["success"])
            checks = {c["name"]: c for c in report["checks"]}
            self.assertEqual(checks["graph_bridge"]["status"], "warning")
            self.assertIn("local-model", checks["model"]["fix"])

    def test_pending_mutation_diagnosis_does_not_overwrite_request(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = self.install_project(root)
            pending = root / "Content/Python/harness.pending.json"
            pending.write_text('{"run_id":"unknown-mutation"}', encoding="utf-8")
            original = (root / "Content/Python/actions.json").read_bytes()
            def forbidden(*args, **kwargs):
                self.fail("pending request overwritten")
            report = diagnose(project, model="test", transport_factory=forbidden, models_loader=lambda **kwargs: ["test"])
            self.assertFalse(report["success"])
            self.assertEqual(original, (root / "Content/Python/actions.json").read_bytes())

    def test_models_uses_get_and_auth_without_generation(self):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append((self.path, self.headers.get("Authorization")))
                body = json.dumps({"data": [{"id": "z"}, {"id": "a"}, {"id": "a"}]}).encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *args): pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            models = list_models(endpoint="http://127.0.0.1:{}/v1/".format(server.server_port), api_key="test-token")
            self.assertEqual(models, ["a", "z"])
            self.assertEqual(requests, [("/v1/models", "Bearer test-token")])
        finally:
            server.shutdown()
            thread.join()
            server.server_close()

    def test_models_rejects_malformed_response(self):
        with patch("unreal_harness.providers.urlopen", return_value=io.BytesIO(b'{"data":[{}]}')):
            with self.assertRaisesRegex(ProviderError, "model IDs"):
                list_models()

    def test_ask_preflight_fails_before_planner_or_transport(self):
        with tempfile.TemporaryDirectory() as directory, patch("unreal_harness.cli.OpenAICompatiblePlanner") as planner, redirect_stdout(io.StringIO()) as output:
            code = main(["ask", "--project", directory, "--json", "create material"])
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(output.getvalue())["phase"], "preflight")
            planner.assert_not_called()

    def test_json_configuration_error_is_valid_json(self):
        with patch("unreal_harness.cli.list_models", side_effect=ProviderError("server unavailable")), redirect_stdout(io.StringIO()) as output:
            code = main(["models", "--json"])
        self.assertEqual(code, 2)
        self.assertFalse(json.loads(output.getvalue())["success"])

    def test_read_summary_shows_object_properties(self):
        summary = _human_summary({"success": True, "read_only": True, "result": {"commands": [
            {"action": "object.inspect", "success": True, "data": {"properties": {"two_sided": True}}}]}})
        self.assertIn('"two_sided": true', summary)

    def test_timeout_flags_are_separate_and_reject_non_finite_values(self):
        args = make_parser().parse_args(["ask", "--project", ".", "--timeout", "10", "--llm-timeout", "180", "inspect"])
        self.assertEqual((args.timeout, args.llm_timeout), (10, 180))
        with redirect_stdout(io.StringIO()), patch("sys.stderr", io.StringIO()):
            for value in ("0", "-1", "nan", "inf"):
                with self.subTest(value=value), self.assertRaises(SystemExit):
                    make_parser().parse_args(["doctor", "--project", ".", "--timeout", value])


if __name__ == "__main__":
    unittest.main()
