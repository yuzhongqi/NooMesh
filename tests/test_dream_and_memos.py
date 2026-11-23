import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from local_learning_runtime.clients import hook
from local_learning_runtime.context import define_theme, packet, session_index
from local_learning_runtime.core import RuntimeErrorBase, Store, canonical, file_digest
from local_learning_runtime.dream import dream_pass
from local_learning_runtime.knowledge import catalog, export_portal
from local_learning_runtime.learning import authorize, forget_workspace, search
from local_learning_runtime.lifecycle import assess
from local_learning_runtime.mcp import respond
from local_learning_runtime.memos import invalidate_memo, propose_memo


class DreamAndMemoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.source = self.repo / "HostConfigProvider.kt"
        self.source.write_text(
            "package sample.host\n\n"
            "class HostConfigProvider {\n"
            "    fun observeInvalidation(): Boolean = true\n"
            "}\n",
            encoding="utf-8",
        )
        self.store = Store(self.root / "state")
        self.store.register("sample/android", self.repo, "android")
        authorize(self.store, ["sample/android"], True)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def memo_spec(self, memo_id="host-boundary", symbols=None):
        return {
            "schema": "llr.memo.v1",
            "id": memo_id,
            "workspace": "sample/android",
            "kind": "trap",
            "title": "HostConfigProvider live invalidation boundary",
            "when": "Modifying host config or feature gate adapters",
            "rule": "Read live feature gate state via observeInvalidation rather than caching at init.",
            "trap": "Snapshotting async config in constructor leaves stale flags after account switch.",
            "tags": ["config", "featuregate"],
            "verification": {
                "method": "source_symbol_contract",
                "summary": "Verified HostConfigProvider and observeInvalidation in source",
                "anchors": [{
                    "path": "HostConfigProvider.kt",
                    "sha256": file_digest(self.source),
                    "symbols": symbols or ["class HostConfigProvider", "fun observeInvalidation"],
                }],
            },
        }

    def test_symbol_anchor_survives_unrelated_edit_as_soft_drift_and_blocks_on_symbol_removal(self):
        # Bogus symbol rejected upfront even if file SHA matches.
        bad = self.memo_spec(symbols=["fun nonExistentSymbol"])
        with self.assertRaisesRegex(RuntimeErrorBase, "Anchor symbol missing"):
            propose_memo(self.store, bad, 0, True)

        published = propose_memo(self.store, self.memo_spec(), 0, True)
        self.assertEqual(published["status"], "PUBLISHED")
        self.assertEqual(assess(self.store, "sample/android", "memo", "host-boundary")["policy"]["knowledge_type"],
                         "source")

        # Also publish a source_audit claim with symbol anchors vs a strict SHA-only claim.
        proof = self.store.observe("ev-1", "sample/android", {"artifact_sha256": "a" * 64},
                                   b'{"verified":true}', "application/json")
        sym_claim = {
            "id": "claim-with-symbols",
            "workspace": "sample/android",
            "task_type": "host-audit",
            "statement": "HostConfigProvider exposes observeInvalidation",
            "conditions": {"artifact_sha256": "a" * 64},
            "anchors": [{"path": "HostConfigProvider.kt", "sha256": file_digest(self.source),
                         "symbols": ["class HostConfigProvider", "fun observeInvalidation"]}],
            "checks": [{"kind": "json_equals", "evidence_id": proof["evidence_id"],
                        "pointer": "/verified", "expected": True}],
            "dependencies": [],
            "provenance": {"kind": "source_audit", "title": "Host audit symbol claim"},
        }
        strict_claim = dict(sym_claim, id="claim-strict-sha",
                            anchors=[{"path": "HostConfigProvider.kt", "sha256": file_digest(self.source)}])
        c1 = self.store.propose(sym_claim)
        self.store.accept(c1["candidate_id"], 0, None, True)
        c2 = self.store.propose(strict_claim)
        self.store.accept(c2["candidate_id"], 0, self.store.revision(), True)
        self.assertEqual(assess(self.store, "sample/android", "claim", "claim-with-symbols")["policy"]["knowledge_type"],
                         "source")

        # Add an unrelated import line to HostConfigProvider.kt -> SHA changes, symbols remain intact.
        self.source.write_text(
            "package sample.host\nimport sample.other.UnrelatedFlag\n\n"
            "class HostConfigProvider {\n"
            "    fun observeInvalidation(): Boolean = true\n"
            "}\n",
            encoding="utf-8",
        )
        found = search(self.store, self.repo, "HostConfigProvider observeInvalidation", "s-drift")
        by_id = {item["id"]: item for item in found["matches"]}
        self.assertEqual(by_id["host-boundary"]["reuse"], "SOFT_DRIFT")
        self.assertEqual(by_id["claim-with-symbols"]["reuse"], "SOFT_DRIFT")
        self.assertEqual(by_id["claim-strict-sha"]["reuse"], "BLOCKED")

        # L2 context ranks SOFT_DRIFT ahead of BLOCKED.
        l2 = packet(self.store, self.repo, "HostConfigProvider observeInvalidation", "s-l2", 2)
        ids = [item["id"] for item in l2["items"]]
        self.assertLess(ids.index("memo:sample/android:host-boundary"),
                        ids.index("claim:claim-strict-sha"))

        # Dry-run dream_pass detects both reanchorable items without mutating versions.
        dry = dream_pass(self.store, "sample/android", apply=False)
        self.assertEqual(len(dry["reanchorable"]), 2)
        self.assertEqual(len(dry["reanchored"]), 0)
        with self.assertRaises(RuntimeErrorBase):
            dream_pass(self.store, "sample/android", apply=True, approve=False)

        # Apply dream_pass -> re-anchors SHA256s to current file and restores APPLICABLE / exact integrity.
        applied = dream_pass(self.store, "sample/android", apply=True, approve=True)
        self.assertEqual(len(applied["reanchored"]), 2)
        refreshed = search(self.store, self.repo, "HostConfigProvider observeInvalidation", "s-fresh")
        by_id_after = {item["id"]: item for item in refreshed["matches"]}
        self.assertEqual(by_id_after["host-boundary"]["reuse"], "APPLICABLE")
        self.assertEqual(by_id_after["claim-with-symbols"]["reuse"], "CONDITIONS_UNKNOWN")

        # Removing the symbol immediately blocks both memo and symbol-anchored claim.
        self.source.write_text("package sample.host\nclass HostConfigProvider {}\n", encoding="utf-8")
        broken = search(self.store, self.repo, "HostConfigProvider observeInvalidation", "s-broken")
        for item in broken["matches"]:
            self.assertEqual(item["reuse"], "BLOCKED")

    def test_memo_privacy_portal_invalidation_and_forget(self):
        leaked = self.memo_spec()
        leaked["trap"] = "Contact fixture@example.com for config"
        with self.assertRaisesRegex(RuntimeErrorBase, "sensitive"):
            propose_memo(self.store, leaked, 0, True)

        propose_memo(self.store, self.memo_spec(), 0, True)
        with self.assertRaisesRegex(RuntimeErrorBase, "version conflict"):
            propose_memo(self.store, self.memo_spec(), 0, True)

        idx = session_index(self.store, self.repo, "HostConfigProvider", "s-idx")
        self.assertEqual(idx["counts"]["memos"], 1)
        self.assertEqual(idx["top_hits"][0]["kind"], "memo")

        out = self.root / "portal-out"
        export_portal(self.store, "sample/android", out)
        html = (out / "index.html").read_text(encoding="utf-8")
        self.assertIn("工程规则与避坑闭环", html)
        self.assertIn("踩坑警告（Trap）", html)

        invalidate_memo(self.store, "sample/android", "host-boundary", 1, "stale", "Replaced", True)
        self.assertEqual(catalog(self.store, "sample/android")["memos"][0]["local_integrity"]["status"],
                         "BLOCKED")

        forget_workspace(self.store, "sample/android", True)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM memos").fetchone()[0], 0)

    def test_theme_cas_bloat_deadlink_remap_and_turnaround_digest(self):
        # Tiny source file so oversized theme triggers Borges bloat warning.
        propose_memo(self.store, self.memo_spec(), 0, True)
        with self.assertRaisesRegex(RuntimeErrorBase, "missing workspace nodes"):
            define_theme(self.store, "sample/android", {
                "id": "host-shard", "title": "Host shard", "summary": "Summary",
                "aliases": ["host"], "members": ["memo:sample/android:missing"],
                "related": [], "gaps": []}, True, strict_links=True)

        # Register theme with legacy claim:<id> link that matches memo:<ws>:<id>, plus bloat summary.
        r1 = define_theme(self.store, "sample/android", {
            "id": "host-shard", "title": "Host shard", "summary": "x" * 400,
            "aliases": ["host"], "members": ["claim:host-boundary", "claim:dead-node"],
            "related": [], "gaps": []}, True, expected_version=0)
        self.assertEqual(r1["version"], 1)
        with self.assertRaisesRegex(RuntimeErrorBase, "version conflict"):
            define_theme(self.store, "sample/android", {
                "id": "host-shard", "title": "Concurrent", "summary": "s",
                "aliases": [], "members": [], "related": [], "gaps": []}, True, expected_version=0)

        # Simulate fail -> pass turnaround in hooks with sensitive token in error line.
        base = {"sessionId": "turn-s1", "cwd": str(self.repo), "timestamp": 10}
        hook(self.store, "copilot", "userPromptSubmitted",
             dict(base, prompt="Fix HostConfigProvider compile error"))
        hook(self.store, "copilot", "postToolUse", dict(base, timestamp=11, toolName="bash",
             toolResult={"resultType": "failure",
                         "textResultForLlm": "e: HostConfigProvider.kt:4: error: unresolved reference; token=secret999"}))
        hook(self.store, "copilot", "postToolUse", dict(base, timestamp=12, toolName="edit",
             toolResult={"resultType": "success", "textResultForLlm": "updated"}))
        hook(self.store, "copilot", "postToolUse", dict(base, timestamp=13, toolName="bash",
             toolResult={"resultType": "success", "textResultForLlm": "BUILD SUCCESSFUL"}))

        mcp_res = respond(self.store, {"method": "tools/call", "params": {
            "name": "local_learning_dream", "arguments": {"workspace": "sample/android"}}})
        report = json.loads(mcp_res["content"][0]["text"])
        self.assertEqual(report["mode"], "DRY_RUN")
        self.assertEqual(len(report["turnaround_episodes"]), 1)
        self.assertNotIn("secret999", json.dumps(report))
        self.assertIn("unresolved reference", report["turnaround_episodes"][0]["error_signature"])
        self.assertEqual(report["theme_repairs"][0]["remapped"],
                         [{"from": "claim:host-boundary", "to": "memo:sample/android:host-boundary"}])
        self.assertEqual(report["theme_repairs"][0]["dangling"], ["claim:dead-node"])

        applied = dream_pass(self.store, "sample/android", apply=True, approve=True)
        self.assertTrue(applied["bloat_warnings"])
        l1 = packet(self.store, self.repo, "host", "reader", 1)
        self.assertEqual(l1["items"][0]["members"], ["memo:sample/android:host-boundary"])
        self.assertEqual(l1["items"][0]["unavailable_members"], [])

    def test_cli_anchor_with_symbols_and_memo_roundtrip(self):
        launcher = Path(__file__).resolve().parents[1] / "tools" / "llr.py"
        cmd = [sys.executable, str(launcher), "--home", str(self.store.home)]
        try:
            anc = subprocess.run(cmd + ["anchor", "sample/android", "HostConfigProvider.kt",
                                        "--symbol", "class HostConfigProvider"],
                                 capture_output=True, text=True, timeout=20)
            self.assertEqual(anc.returncode, 0, anc.stderr)
            anchor_obj = json.loads(anc.stdout)
            self.assertEqual(anchor_obj["symbols"], ["class HostConfigProvider"])
        finally:
            subprocess.run(cmd + ["host-stop"], capture_output=True, timeout=10)

    def test_host_idle_auto_dream_runs_before_shutdown_with_cooldown_and_receipt(self):
        import threading
        from local_learning_runtime.clients import doctor
        from local_learning_runtime.host import AgentServer

        propose_memo(self.store, self.memo_spec(), 0, True)
        authorize(self.store, ["sample/android"], True,
                  auto_dream_source=True, dream_interval_seconds=3600)
        # Introduce unrelated edit so memo enters SOFT_DRIFT.
        self.source.write_text(
            "package sample.host\n// idle consolidation check\n"
            "class HostConfigProvider {\n"
            "    fun observeInvalidation(): Boolean = true\n"
            "}\n",
            encoding="utf-8",
        )
        self.assertEqual(
            search(self.store, self.repo, "HostConfigProvider", "pre-idle")["matches"][0]["reuse"],
            "SOFT_DRIFT")

        server = AgentServer(self.store.home, idle_seconds=0.15)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        server.server_close()

        after = search(self.store, self.repo, "HostConfigProvider", "post-idle")["matches"][0]
        self.assertEqual(after["reuse"], "APPLICABLE")
        self.assertEqual(after["version"], 2)
        diag = doctor(self.store)
        self.assertTrue(diag["auto_dream_source"])
        idle_receipt = next(r for r in diag["recent_handoffs"] if r["event"] == "autoDreamPass")
        self.assertEqual(idle_receipt["client"], "host-idle")
        self.assertEqual(idle_receipt["reanchored"], 1)

        # Immediate second idle shutdown respects cooldown and does not bump version again.
        self.source.write_text(
            "package sample.host\n// second edit within cooldown\n"
            "class HostConfigProvider {\n"
            "    fun observeInvalidation(): Boolean = true\n"
            "}\n",
            encoding="utf-8",
        )
        server2 = AgentServer(self.store.home, idle_seconds=0.15)
        thread2 = threading.Thread(target=server2.serve_forever, daemon=True)
        thread2.start()
        thread2.join(timeout=5)
        self.assertFalse(thread2.is_alive())
        server2.server_close()

        throttled = search(self.store, self.repo, "HostConfigProvider", "post-cooldown")["matches"][0]
        self.assertEqual(throttled["version"], 2)
        self.assertEqual(throttled["reuse"], "SOFT_DRIFT")


if __name__ == "__main__":
    unittest.main()
