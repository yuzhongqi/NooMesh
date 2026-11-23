import http.server
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from local_learning_runtime.clients import codex_notify, doctor, hook
from local_learning_runtime.core import RuntimeErrorBase, Store
from local_learning_runtime.learning import authorize
from local_learning_runtime.missions import control, create_mission, tick
from local_learning_runtime.notifier import (configure_notifier, maybe_notify_mission,
                                             notifier_status, validate_webhook_url)


class _WebhookHandler(http.server.BaseHTTPRequestHandler):
    received = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        self.__class__.received.append({"path": self.path, "body": body})
        self.send_response(202)
        self.end_headers()
        self.wfile.write(b"Accepted")

    def log_message(self, *args):
        return


class NotifierTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        (self.repo / "a.json").write_text("{}\n", encoding="utf-8")
        self.store = Store(self.root / "state")
        self.store.register("sample/android", self.repo, "android")
        authorize(self.store, ["sample/android"], True)
        _WebhookHandler.received = []
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _WebhookHandler)
        self.port = self.httpd.server_address[1]
        self.server_thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.server_thread.start()
        self.webhook_url = f"http://127.0.0.1:{self.port}/workflows/test/triggers/manual?sig=super_secret_sas"

    def tearDown(self):
        self.httpd.shutdown()
        self.server_thread.join(timeout=5)
        self.httpd.server_close()
        self.store.close()
        self.tmp.cleanup()

    def test_domain_allowlist_and_sas_secret_redaction(self):
        for valid in (
            "https://prod-01.westus.logic.azure.com:443/workflows/abc/triggers/manual?sig=sas123",
            "https://default123.00.environment.api.powerplatform.com:443/powerautomate/automations/direct?sig=sas123",
        ):
            checked = validate_webhook_url(valid)
            self.assertNotIn("sas123", checked["masked_url"])
        for invalid in (
            "http://prod-01.westus.logic.azure.com/workflows/abc",
            "https://evil.example.com/workflows/abc?sig=1",
            "https://user:pass@prod-01.westus.logic.azure.com/workflows/abc",
        ):
            with self.assertRaises(RuntimeErrorBase):
                validate_webhook_url(invalid)

        status = configure_notifier(self.store, True, self.webhook_url, min_duration_seconds=30)
        self.assertTrue(status["enabled"])
        self.assertNotIn("super_secret_sas", json.dumps(status))
        self.assertNotIn("super_secret_sas", json.dumps(doctor(self.store)))

    def test_tenant_authenticated_webhook_attaches_az_bearer_token_without_logging_it(self):
        from unittest.mock import patch
        from local_learning_runtime.notifier import send_test_notification
        tenant_url = "https://env01.e4.environment.api.powerplatform.com:443/powerautomate/automations/direct/workflows/wf1/triggers/manual/paths/invoke?api-version=1"
        configure_notifier(self.store, True, tenant_url, min_duration_seconds=30)
        captured_headers = {}

        class _FakeResp:
            status = 202
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self, limit=4096):
                return b"Accepted"

        class _FakeOpener:
            def open(self, req, timeout=5.0):
                captured_headers.update(dict(req.header_items()))
                return _FakeResp()

        with patch("local_learning_runtime.notifier._acquire_flow_bearer_token",
                   return_value="synthetic-aad-jwt"), \
                patch("urllib.request.build_opener", return_value=_FakeOpener()):
            res = send_test_notification(self.store, True)
        self.assertEqual(res["http_status"], 202)
        self.assertEqual(captured_headers.get("Authorization"), "Bearer synthetic-aad-jwt")
        self.assertNotIn("synthetic-aad-jwt", json.dumps(notifier_status(self.store)))

    def test_min_duration_filter_redaction_and_codex_deduplication(self):
        configure_notifier(self.store, True, self.webhook_url, min_duration_seconds=30)

        # 1) Fast turn (<30s) is skipped silently.
        fast = {"sessionId": "fast-s", "cwd": str(self.repo), "timestamp": 1}
        hook(self.store, "copilot", "userPromptSubmitted", dict(fast, prompt="Quick question"))
        hook(self.store, "copilot", "agentStop", dict(fast, timestamp=2))
        time.sleep(0.3)
        self.assertEqual(len(_WebhookHandler.received), 0)

        # 2) Long turn (>=30s) dispatches Adaptive Card once, redacts secrets, and deduplicates Stop + notify.
        slow = {"session_id": "codex-long", "cwd": str(self.repo), "timestamp": 10}
        hook(self.store, "codex-hooks", "userPromptSubmitted",
             dict(slow, prompt="Run Android verification with email=fixture@example.com"))
        backdated = (datetime.now(timezone.utc) - timedelta(seconds=75)).isoformat()
        with self.store.transaction():
            self.store.db.execute("UPDATE client_receipts SET created_at=? WHERE session='codex-long'",
                                  (backdated,))
        hook(self.store, "codex-hooks", "agentStop", dict(slow, timestamp=85))
        codex_notify(self.store, {"thread-id": "codex-long", "cwd": str(self.repo), "timestamp": 86})

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not _WebhookHandler.received:
            time.sleep(0.05)
        time.sleep(0.25)
        self.assertEqual(len(_WebhookHandler.received), 1)
        payload = _WebhookHandler.received[0]["body"]
        self.assertEqual(payload["type"], "message")
        self.assertEqual(payload["attachments"][0]["contentType"],
                         "application/vnd.microsoft.card.adaptive")
        self.assertEqual(payload["llr"]["client"], "Codex")
        self.assertEqual(payload["llr"]["workspace"], "sample/android")
        self.assertGreaterEqual(payload["llr"]["duration_seconds"], 70)
        self.assertNotIn("fixture@example.com", json.dumps(payload))
        self.assertEqual(notifier_status(self.store)["last_delivery"]["http_status"], 202)

    def test_cli_webhook_stdin_test_ping_and_mission_completion(self):
        launcher = Path(__file__).resolve().parents[1] / "tools" / "llr.py"
        cmd = [sys.executable, str(launcher), "--home", str(self.store.home)]
        try:
            cfg = subprocess.run(
                cmd + ["notify-config", "--webhook-stdin", "--min-duration", "15", "--approve"],
                input=self.webhook_url, capture_output=True, text=True, timeout=20,
            )
            self.assertEqual(cfg.returncode, 0, cfg.stderr)
            self.assertNotIn("super_secret_sas", cfg.stdout)

            ping = subprocess.run(cmd + ["notify-test", "--approve"],
                                  capture_output=True, text=True, timeout=20)
            self.assertEqual(ping.returncode, 0, ping.stderr)
            self.assertEqual(json.loads(ping.stdout)["http_status"], 202)
            self.assertEqual(len(_WebhookHandler.received), 1)

            # Mission completion also triggers a deduplicated notification.
            spec = {
                "schema": "llr.mission.v1", "id": "m-notify", "workspace": "sample/android",
                "goal": "Inventory JSON files",
                "deadline": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                "permissions": {"read_source": True, "run_validators": False, "allow_model": False},
                "budgets": {"max_items": 2, "max_seconds": 30, "max_files": 10,
                            "max_model_calls": 0, "max_packet_bytes": 4000, "max_spend_micro": 0},
                "questions": [{"kind": "inventory", "goal": "Scan", "glob": "*.json"}],
            }
            create_mission(self.store, spec)
            control(self.store, "m-notify", "approve", True)
            control(self.store, "m-notify", "start")
            tick(self.store, "m-notify")
            tick(self.store, "m-notify")
            res1 = maybe_notify_mission(self.store, "m-notify", sync=True)
            res2 = maybe_notify_mission(self.store, "m-notify", sync=True)
            self.assertEqual(res1["status"], "SENT")
            self.assertEqual(res2["status"], "ALREADY_NOTIFIED")
            self.assertEqual(len(_WebhookHandler.received), 2)
        finally:
            subprocess.run(cmd + ["host-stop"], capture_output=True, timeout=10)


if __name__ == "__main__":
    unittest.main()
