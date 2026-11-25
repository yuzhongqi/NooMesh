import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from local_learning_runtime.core import Store, file_digest
from local_learning_runtime.inbox import (claim_proactive_message, configure_inbox,
                                          parse_proactive_directive, poll_proactive_inbox,
                                          register_live_session)
from local_learning_runtime.learning import authorize
from local_learning_runtime.mcp import respond
from local_learning_runtime.solutions import execute_solution, propose_solution


class ProactiveInboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo_a = self.root / "officemobile"
        self.repo_b = self.root / "ocm"
        self.repo_a.mkdir()
        self.repo_b.mkdir()
        (self.repo_a / "bxlconfig.json").write_text("{}\n", encoding="utf-8")
        self.store = Store(self.root / "state")
        self.store.register("officemobile/android", self.repo_a, "android")
        self.store.register("ocm/android", self.repo_b, "android")
        authorize(self.store, ["officemobile/android", "ocm/android"], True)
        configure_inbox(
            self.store, True,
            reader_url="http://127.0.0.1:9999/reader?sig=secret123",
            poll_seconds=5, allow_new_sessions=True,
            default_cwd=str(self.repo_a), machine_name="MBP-3",
        )

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_directive_parsing_routes_to_matching_live_session_and_ignores_bot_or_other_mac(self):
        d = parse_proactive_directive("@MBP-3 #abcd1234 check git status")
        self.assertEqual(d["machine_tag"], "MBP-3")
        self.assertEqual(d["session_prefix"], "abcd1234")
        self.assertEqual(d["prompt"], "check git status")
        self.assertTrue(parse_proactive_directive("/new @ocm inspect WidgetService")["force_new"])

        register_live_session(self.store, "sess-om-1111", "copilot", str(self.repo_a), "Theme setup")
        register_live_session(self.store, "sess-ocm-2222", "copilot", str(self.repo_b), "OCM SDK banner")

        # 1) First poll initializes cursor without replaying historical chat messages.
        init_res = poll_proactive_inbox(self.store, messages_override=[
            {"id": "100", "messageType": "message", "from": {"user": {"displayName": "User"}},
             "body": {"content": "<p>old message</p>"}},
        ])
        self.assertEqual(init_res["status"], "INITIALIZED_CURSOR")
        self.assertEqual(claim_proactive_message(self.store, "sess-ocm-2222")["status"], "EMPTY")

        # 2) Bot message, other-Mac message, and domain-matched OCM message arrive.
        second = poll_proactive_inbox(self.store, messages_override=[
            {"id": "101", "messageType": "message", "from": {"application": {"displayName": "Workflows"}},
             "body": {"content": "<p>Bot notification</p>"}},
            {"id": "102", "messageType": "message", "from": {"user": {"displayName": "User"}},
             "body": {"content": "<p>@MacStudio check status</p>"}},
            {"id": "103", "messageType": "message", "from": {"user": {"displayName": "User"}},
             "body": {"content": "<p>@MBP-3 check ocm banner logic</p>"}},
        ])
        actions = {item["message_id"]: item for item in second["processed"]}
        self.assertNotIn("101", actions)
        self.assertEqual(actions["102"]["action"], "IGNORED_OTHER_MACHINE")
        self.assertEqual(actions["103"]["action"], "QUEUED_TO_LIVE_SESSION")
        self.assertEqual(actions["103"]["session"], "sess-ocm-2222")

        claimed = claim_proactive_message(self.store, "sess-ocm-2222")
        self.assertEqual(claimed["status"], "CLAIMED")
        self.assertEqual(claimed["prompt"], "check ocm banner logic")
        self.assertEqual(claim_proactive_message(self.store, "sess-ocm-2222")["status"], "EMPTY")

    def test_spawns_new_session_when_no_live_session_or_forced_new(self):
        poll_proactive_inbox(self.store, messages_override=[{"id": "200"}])
        with patch("local_learning_runtime.inbox._spawn_headless_worker") as spawner:
            out = poll_proactive_inbox(self.store, messages_override=[
                {"id": "201", "messageType": "message",
                 "from": {"user": {"displayName": "User"}},
                 "body": {"content": "<p>/new @ocm summarize recent commits</p>"}},
            ])
        self.assertEqual(out["processed"][0]["action"], "SPAWNED_NEW_SESSION")
        self.assertEqual(out["processed"][0]["cwd"], str(self.repo_b.resolve()))
        spawner.assert_called_once()

    def test_mcp_string_level_coercion_and_whitespace_invocation_normalization(self):
        # String "0" / "2" from VS Code MCP schema coercion succeeds without -32602.
        ctx_res = respond(self.store, {"method": "tools/call", "params": {
            "name": "local_experience_context",
            "arguments": {"cwd": str(self.repo_a), "query": "android", "session": "s-mcp", "level": "0"},
        }})
        self.assertFalse(ctx_res["isError"])

        # Invocation containing spaces is normalized cleanly instead of failing validation.
        sha = file_digest(self.repo_a / "bxlconfig.json")
        spec = {
            "schema": "llr.solution.v1", "id": "cfg-id", "workspace": "officemobile/android",
            "problem": {"intent": "cfg", "goal": "Verify bxlconfig", "patterns": ["check {p}"]},
            "parameters": {"p": {"type": "string", "enum": ["bxlconfig.json"]}},
            "preconditions": [{"kind": "file_sha256", "path": {"input": "p"}, "expected": sha}],
            "steps": [{"id": "s1", "op": "inspect_file", "path": {"input": "p"}}],
            "postconditions": [{"step": "s1", "pointer": "/sha256", "expected": sha}],
            "result": {"sha256": {"step": "s1", "pointer": "/sha256"}},
            "boundaries": ["bxlconfig only"],
        }
        propose_solution(self.store, spec, 0, True)
        execute_solution(self.store, "officemobile/android", "cfg-id", "train", "c1",
                         {"p": "bxlconfig.json"}, {}, True, True)
        exec_res = respond(self.store, {"method": "tools/call", "params": {
            "name": "local_experience_execute",
            "arguments": {
                "workspace": "officemobile/android", "solution": "cfg-id",
                "session": "s-mcp", "invocation": "eval task 3a with spaces",
                "parameters": {"p": "bxlconfig.json"}, "conditions": {},
            },
        }})
        self.assertFalse(exec_res["isError"])
        self.assertIn("LOCAL_SOLVED", exec_res["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
