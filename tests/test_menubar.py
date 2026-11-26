import tempfile
import unittest
from pathlib import Path

from local_learning_runtime.clients import hook
from local_learning_runtime.core import Store, file_digest
from local_learning_runtime.discovery import register_alias
from local_learning_runtime.inbox import register_live_session
from local_learning_runtime.learning import authorize, search
from local_learning_runtime.menubar import (menubar_hits_detail, menubar_item_detail,
                                            menubar_snapshot, open_live_portal,
                                            probe_menubar_running)
from local_learning_runtime.memos import propose_memo
from local_learning_runtime.solutions import propose_solution


class MenuBarDashboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.mono_root = self.root / "monorepo"
        self.android_repo = self.mono_root / "android"
        self.ios_repo = self.mono_root / "ios"
        self.android_repo.mkdir(parents=True)
        self.ios_repo.mkdir(parents=True)
        self.anchor_file = self.android_repo / "Initializer.kt"
        self.anchor_file.write_text("object Initializer {\n    fun boot() = true\n}\n", encoding="utf-8")

        self.store = Store(self.root / "state")
        self.store.register("demo/android", self.android_repo, "android")
        self.store.register("demo/ios", self.ios_repo, "ios")
        register_alias(self.store, "demo/android", self.mono_root, True, True)
        register_alias(self.store, "demo/ios", self.mono_root, True, True)
        authorize(self.store, ["demo/android", "demo/ios"], True, auto_dream_source=True)

        propose_memo(self.store, {
            "schema": "llr.memo.v1",
            "id": "boot-once-guard",
            "workspace": "demo/android",
            "kind": "trap",
            "title": "Initializer one-shot boot guard",
            "when": "Calling Initializer.boot() on warm intent re-entry",
            "rule": "Reset state explicitly or pass refreshed config before re-routing.",
            "trap": "Initializer.boot() is a no-op on subsequent calls and leaves stale state.",
            "tags": ["initializer", "boot", "intent"],
            "verification": {
                "method": "source_symbol_contract",
                "summary": "Verified Initializer.boot guard in source",
                "anchors": [{
                    "path": "Initializer.kt",
                    "sha256": file_digest(self.anchor_file),
                    "symbols": ["Initializer", "boot"],
                }],
            },
        }, expected_version=0, approve=True)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_menubar_snapshot_tracks_active_tasks_and_resolved_knowledge_hits(self):
        # Record a descriptive task run via the real IDE hook followed by a 2-char confirmation.
        hook(self.store, "copilot", "userPromptSubmitted", {
            "sessionId": "sess12345678",
            "cwd": str(self.android_repo),
            "prompt": "Fix warm intent routing regression in Initializer",
        })
        register_live_session(
            self.store, "sess12345678", "copilot-sdk",
            str(self.android_repo), "继续", state="active",
        )

        # Trigger a search from the shared monorepo root using explicit platform condition
        # (without repeating 'android' in the query string).
        found = search(
            self.store, self.mono_root,
            "Initializer boot warm intent",
            "sess12345678",
            actual={"platform": "android"},
        )
        self.assertEqual(found["status"], "FOUND")
        self.assertTrue(any(m["id"] == "boot-once-guard" for m in found["matches"]))

        snap = menubar_snapshot(self.store)
        self.assertEqual(snap["schema"], "llr.menubar.v1")
        self.assertEqual(snap["primary_workspace"], "demo/android")
        self.assertTrue(snap["host"]["auto_dream"])
        self.assertFalse(snap["host"]["online"])
        self.assertTrue(snap["just_hit_knowledge"])
        self.assertGreaterEqual(snap["totals"]["today_hits"], 1)
        self.assertEqual(snap["totals"]["memos"], 1)
        self.assertEqual(snap["totals"]["active_sessions"], 1)

        self.assertEqual(len(snap["live_sessions"]), 1)
        live = snap["live_sessions"][0]
        self.assertEqual(live["session_short"], "#sess1234")
        self.assertEqual(live["state"], "active")
        # Short reply "继续" falls back to the descriptive task title from task_runs.
        self.assertIn("Fix warm intent routing regression", live["title"])
        self.assertTrue(any(
            k["subject"] == "boot-once-guard" and k["title"] == "Initializer one-shot boot guard"
            for k in live["knowledge_used"]
        ))

        self.assertTrue(any(
            act["subject"] == "boot-once-guard"
            and act["kind"] == "memo"
            and "Fix warm intent routing regression" in act["task_title"]
            for act in snap["recent_activity"]
        ))

    def test_open_live_portal_exports_html_and_probe_menubar_handles_clean_state(self):
        portal_res = open_live_portal(self.store, workspace="demo/android", open_browser=False)
        self.assertEqual(portal_res["status"], "PORTAL_READY")
        self.assertTrue(Path(portal_res["html"]).is_file())

        probed = probe_menubar_running(self.store.home)
        self.assertFalse(probed["running"])
        self.assertIsNone(probed["pid"])

    def test_menubar_item_detail_returns_complete_memo_claim_and_solution_without_hit_inflation(self):
        # Seed a verified Claim and a Solution in demo/android as well.
        conds = {"platform": "android", "artifact_sha256": file_digest(self.anchor_file)}
        ev = self.store.observe(
            "ev-init-1", "demo/android", conds,
            b'{"verified": true, "flag": "warm_reset"}', "application/json",
        )
        proposed_claim = self.store.propose({
            "id": "warm-intent-claim",
            "workspace": "demo/android",
            "task_type": "intent-routing",
            "statement": "Warm intent re-entry must refresh configuration before routing.",
            "conditions": conds,
            "anchors": [{
                "path": "Initializer.kt",
                "sha256": file_digest(self.anchor_file),
                "symbols": ["Initializer", "boot"],
            }],
            "checks": [{
                "kind": "json_equals",
                "evidence_id": ev["evidence_id"],
                "pointer": "/verified",
                "expected": True,
            }],
            "dependencies": [],
            "provenance": {
                "title": "Warm intent config refresh claim",
                "kind": "source_audit",
                "task": "Audit warm intent",
            },
        })
        self.store.accept(proposed_claim["candidate_id"], 0, None, True)

        propose_solution(self.store, {
            "schema": "llr.solution.v1",
            "id": "check-init-baseline",
            "workspace": "demo/android",
            "problem": {
                "intent": "init-baseline",
                "goal": "Verify Initializer.kt baseline digest",
                "patterns": ["check Initializer baseline"],
            },
            "parameters": {},
            "preconditions": [{
                "kind": "file_sha256",
                "path": "Initializer.kt",
                "expected": file_digest(self.anchor_file),
            }],
            "steps": [{"id": "s1", "op": "inspect_file", "path": "Initializer.kt"}],
            "postconditions": [{
                "step": "s1",
                "pointer": "/sha256",
                "expected": file_digest(self.anchor_file),
            }],
            "result": {"ok": True},
            "boundaries": ["Only checks file digest"],
        }, expected_version=0, approve=True)

        hits_before = menubar_snapshot(self.store)["totals"]["today_hits"]

        # Append an unrelated comment to Initializer.kt to trigger SOFT_DRIFT while keeping symbols.
        self.anchor_file.write_text(
            "// unrelated doc note\nobject Initializer {\n    fun boot() = true\n}\n",
            encoding="utf-8",
        )

        memo_detail = menubar_item_detail(
            self.store, "memo", "boot-once-guard", workspace="demo/android"
        )
        self.assertEqual(memo_detail["schema"], "llr.menubar_item.v1")
        self.assertEqual(memo_detail["integrity_status"], "SOFT_DRIFT")
        self.assertEqual(len(memo_detail["sections"]), 4)
        self.assertIn("is a no-op on subsequent calls", memo_detail["sections"][2]["body"])
        self.assertEqual(memo_detail["anchors"][0]["full_path"], str(self.anchor_file.resolve()))
        self.assertIn("Initializer", memo_detail["anchors"][0]["symbols"])
        self.assertIn("# [避坑 Memo] Initializer one-shot boot guard", memo_detail["copy_markdown"])

        claim_detail = menubar_item_detail(self.store, "claim", "warm-intent-claim")
        self.assertEqual(claim_detail["integrity_status"], "SOFT_DRIFT")
        self.assertIn("Warm intent re-entry must refresh", claim_detail["sections"][0]["body"])
        self.assertIn("warm_reset", claim_detail["sections"][2]["body"])

        sol_detail = menubar_item_detail(
            self.store, "solution", "check-init-baseline", workspace="demo/android"
        )
        self.assertEqual(sol_detail["kind"], "solution")
        self.assertIn("Verify Initializer.kt baseline digest", sol_detail["title"])
        self.assertIn("Only checks file digest", sol_detail["sections"][3]["body"])

        hits_after = menubar_snapshot(self.store)["totals"]["today_hits"]
        self.assertEqual(hits_before, hits_after)

    def test_menubar_hits_detail_aggregates_24h_channels_top_items_and_queries(self):
        # 1) Session index L0 hook injection hit
        hook(self.store, "copilot", "sessionStart", {
            "sessionId": "sess-alpha-1111",
            "cwd": str(self.android_repo),
            "initialPrompt": "Fix Initializer boot on warm intent",
        })
        # 2) Explicit search hit in another session (search() records retrieval receipt automatically)
        hook(self.store, "copilot", "userPromptSubmitted", {
            "sessionId": "sess-beta-2222",
            "cwd": str(self.android_repo),
            "prompt": "Audit warm intent re-entry in Initializer",
        })
        search(
            self.store, self.android_repo,
            "Initializer boot warm intent re-entry",
            "sess-beta-2222",
            actual={"platform": "android"},
        )

        snap = menubar_snapshot(self.store)
        report = menubar_hits_detail(self.store, hours=24)
        self.assertEqual(report["schema"], "llr.menubar_hits.v1")
        self.assertEqual(report["window_hits"], snap["totals"]["today_hits"])
        self.assertGreaterEqual(report["window_hits"], 2)
        self.assertEqual(report["distinct_items"], 1)
        self.assertEqual(report["distinct_sessions"], 2)
        self.assertEqual(report["by_kind"]["memo"], report["window_hits"])

        top = report["top_items"][0]
        self.assertEqual(top["subject"], "boot-once-guard")
        self.assertEqual(top["hit_count"], report["window_hits"])
        self.assertEqual(top["sessions_count"], 2)

        channel_keys = {c["key"] for c in report["channels"]}
        self.assertIn("search", channel_keys)
        self.assertIn("session_index", channel_keys)

        self.assertTrue(any(
            ev["query"] == "Initializer boot warm intent re-entry"
            and "Audit warm intent re-entry" in ev["task_title"]
            for ev in report["events"]
        ))
        self.assertIn("# 近 24 小时本地知识命中报告", report["copy_markdown"])


if __name__ == "__main__":
    unittest.main()
