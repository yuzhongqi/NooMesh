import http.server
import json
import tempfile
import threading
import unittest
from pathlib import Path

from local_learning_runtime.clients import hook
from local_learning_runtime.core import Store
from local_learning_runtime.learning import authorize
from local_learning_runtime.notifier import configure_notifier
from local_learning_runtime.relay import (build_interactive_card, configure_relay,
                                          poll_interactive_run, relay_status,
                                          send_interactive_relay)


class _MockFlowServer(http.server.BaseHTTPRequestHandler):
    posted_cards = []
    run_state = "Running"
    reply_payload = {}

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        self.__class__.posted_cards.append(body)
        self.send_response(202)
        self.send_header("x-ms-workflow-run-id", "run-abc-123")
        self.end_headers()
        self.wfile.write(b"Accepted")

    def do_GET(self):
        if "/actions/Compose_Reply" in self.path:
            out = {"properties": {"outputs": self.__class__.reply_payload}}
        else:
            out = {"properties": {"status": self.__class__.run_state}}
        raw = json.dumps(out).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        return


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.store = Store(self.root / "state")
        self.store.register("sample/android", self.repo, "android")
        authorize(self.store, ["sample/android"], True)
        _MockFlowServer.posted_cards = []
        _MockFlowServer.run_state = "Running"
        _MockFlowServer.reply_payload = {}
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _MockFlowServer)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        self.httpd.shutdown()
        self.thread.join(timeout=5)
        self.httpd.server_close()
        self.store.close()
        self.tmp.cleanup()

    def test_interactive_card_send_poll_and_privacy_redaction(self):
        cfg = configure_relay(
            self.store, True, f"{self.base_url}/trigger?sig=secret_sas",
            environment_id="env-1", flow_id="flow-1", min_duration_seconds=5,
        )
        self.assertTrue(cfg["enabled"])
        self.assertNotIn("secret_sas", json.dumps(relay_status(self.store)))

        sent = send_interactive_relay(
            self.store, "sess-1", "Decision needed",
            "Inspect user fixture@example.com options",
            choices=[{"label": "Option A", "value": "opt_a"}, {"label": "Option B", "value": "opt_b"}],
        )
        self.assertEqual(sent["status"], "WAITING")
        self.assertEqual(sent["run_id"], "run-abc-123")
        self.assertNotIn("fixture@example.com", json.dumps(_MockFlowServer.posted_cards[0]))

        # 1) While running -> RUNNING
        polled1 = poll_interactive_run(self.store, "run-abc-123", base_api_url=self.base_url)
        self.assertEqual(polled1["status"], "RUNNING")

        # 2) User selects Option A + adds custom text -> REPLIED with combined prompt
        _MockFlowServer.run_state = "Succeeded"
        _MockFlowServer.reply_payload = {
            "request_id": sent["request_id"],
            "response": {
                "data": {"choice": "opt_a", "reply_text": "also run unit tests"},
                "responder": {"userPrincipalName": "dev@example.com"},
            },
        }
        polled2 = poll_interactive_run(self.store, "run-abc-123", base_api_url=self.base_url)
        self.assertEqual(polled2["status"], "REPLIED")
        self.assertTrue(polled2["should_continue"])
        self.assertEqual(polled2["prompt"], "[Teams 选择: opt_a] also run unit tests")

        # 3) Selecting __stop__ with empty reply_text cleanly ends without continuation
        _MockFlowServer.reply_payload = {
            "request_id": sent["request_id"],
            "response": {"data": {"choice": "__stop__", "reply_text": ""}},
        }
        polled3 = poll_interactive_run(self.store, "run-abc-123", base_api_url=self.base_url)
        self.assertFalse(polled3["should_continue"])
        self.assertEqual(polled3["prompt"], "")

    def test_one_way_notifier_yields_to_registered_interactive_relay_session(self):
        configure_notifier(self.store, True, f"{self.base_url}/notify", min_duration_seconds=0)
        configure_relay(self.store, True, f"{self.base_url}/relay",
                        environment_id="env-1", flow_id="flow-1", min_duration_seconds=0)
        with self.store.transaction():
            self.store.db.execute("INSERT INTO state VALUES ('relay_session:s-interactive', 'now')")
        hook(self.store, "copilot", "userPromptSubmitted",
             {"sessionId": "s-interactive", "cwd": str(self.repo), "prompt": "Android check"})
        hook(self.store, "copilot", "agentStop",
             {"sessionId": "s-interactive", "cwd": str(self.repo)})
        # One-way notifier must NOT duplicate-post when interactive relay owns the copilot session.
        self.assertEqual(len(_MockFlowServer.posted_cards), 0)
        card = build_interactive_card(self.store, "s-interactive", "Done", "Summary")
        self.assertEqual(card["card"]["type"], "AdaptiveCard")


if __name__ == "__main__":
    unittest.main()
