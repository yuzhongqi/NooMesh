import tempfile
import unittest
from pathlib import Path

from local_learning_runtime.clients import hook
from local_learning_runtime.core import Store, file_digest
from local_learning_runtime.discovery import register_alias
from local_learning_runtime.inbox import register_live_session
from local_learning_runtime.learning import authorize, search
from local_learning_runtime.menubar import menubar_snapshot, open_live_portal, probe_menubar_running
from local_learning_runtime.memos import propose_memo


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


if __name__ == "__main__":
    unittest.main()
