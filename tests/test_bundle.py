import io
import json
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

from local_learning_runtime.bundle import (export_bundle, import_bundle,
                                           inspect_bundle, list_conflicts)
from local_learning_runtime.context import define_theme
from local_learning_runtime.core import RuntimeErrorBase, Store, canonical, digest, file_digest
from local_learning_runtime.learning import authorize, register_runner, search
from local_learning_runtime.menubar import menubar_snapshot
from local_learning_runtime.memos import propose_memo
from local_learning_runtime.solutions import execute_solution, propose_solution, route


class PortableBundleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

        # Simulate Mac A checkout at /.../mac_a/repo
        self.repo_a = self.root / "mac_a" / "repo"
        self.repo_a.mkdir(parents=True)
        (self.repo_a / "NetworkService.kt").write_text(
            "class NetworkService {\n    fun connect() = true\n}\n", encoding="utf-8"
        )
        (self.repo_a / "FeatureOnlyOnBranchA.kt").write_text(
            "object FeatureOnlyOnBranchA {\n    const val ENABLED = true\n}\n", encoding="utf-8"
        )
        (self.repo_a / "bxlconfig.json").write_text('{"SealSourceDirectory": false}\n', encoding="utf-8")

        self.store_a = Store(self.root / "mac_a_state")
        self.store_a.register("demo/android", self.repo_a, "android")
        authorize(self.store_a, ["demo/android"], True)

        # Simulate Mac B checkout at a completely different path (/.../mac_b/checkout):
        # - NetworkService.kt has extra comments (different SHA-256, same symbols -> SOFT_DRIFT!)
        # - FeatureOnlyOnBranchA.kt does NOT exist on Mac B yet (-> BRANCH_MISMATCH!)
        # - bxlconfig.json is identical (-> APPLICABLE zero-model P/R/Q execution!)
        self.repo_b = self.root / "mac_b" / "checkout"
        self.repo_b.mkdir(parents=True)
        (self.repo_b / "NetworkService.kt").write_text(
            "// Local edit on Mac B\nclass NetworkService {\n    fun connect() = true\n}\n",
            encoding="utf-8",
        )
        (self.repo_b / "bxlconfig.json").write_text('{"SealSourceDirectory": false}\n', encoding="utf-8")

        self.store_b = Store(self.root / "mac_b_state")
        self.store_b.register("demo/android", self.repo_b, "android")
        authorize(self.store_b, ["demo/android"], True)

    def tearDown(self):
        self.store_a.close()
        self.store_b.close()
        self.tmp.cleanup()

    def _seed_mac_a_doctrine(self):
        # 1) Memo anchored to NetworkService.kt (with 25,000 acquisition tokens)
        propose_memo(self.store_a, {
            "schema": "llr.memo.v1",
            "id": "emulator-dns-deadlock",
            "workspace": "demo/android",
            "kind": "trap",
            "title": "Android emulator no network: stale netsimd and private DNS deadlock",
            "when": "Switching host Wi-Fi networks leaves Android emulator offline",
            "rule": "Disable private_dns_mode, stop orphan netsimd, and pass active host DNS.",
            "trap": "Orphan netsimd keeps forwarding to stale gateway.",
            "tags": ["emulator", "dns", "netsimd"],
            "acquisition_tokens": 25_000,
            "verification": {
                "method": "incident_fix",
                "summary": "Verified across Wi-Fi transitions",
                "anchors": [{
                    "path": "NetworkService.kt",
                    "sha256": file_digest(self.repo_a / "NetworkService.kt"),
                    "symbols": ["class NetworkService", "fun connect"],
                }],
            },
        }, expected_version=0, approve=True)

        # 2) Memo anchored to FeatureOnlyOnBranchA.kt (missing on Mac B)
        propose_memo(self.store_a, {
            "schema": "llr.memo.v1",
            "id": "new-branch-only-rule",
            "workspace": "demo/android",
            "kind": "rule",
            "title": "Branch A feature flag: keep ENABLED gated",
            "when": "Touching FeatureOnlyOnBranchA",
            "rule": "Check ENABLED flag before wiring.",
            "trap": "Ungated activation on older branches.",
            "tags": ["branch_a"],
            "acquisition_tokens": 8_000,
            "verification": {
                "method": "source_symbol_contract",
                "summary": "Verified on Branch A",
                "anchors": [{
                    "path": "FeatureOnlyOnBranchA.kt",
                    "sha256": file_digest(self.repo_a / "FeatureOnlyOnBranchA.kt"),
                    "symbols": ["object FeatureOnlyOnBranchA"],
                }],
            },
        }, expected_version=0, approve=True)

        # 3) Source-anchored Claim + JSON evidence blob
        conds = {
            "platform": "android",
            "artifact_sha256": file_digest(self.repo_a / "NetworkService.kt"),
        }
        ev = self.store_a.observe(
            "ev-net-1", "demo/android", conds,
            b'{"verified": true, "component": "NetworkService"}',
            "application/json",
        )
        cand = self.store_a.propose({
            "id": "network-service-singleton-claim",
            "workspace": "demo/android",
            "task_type": "network-architecture",
            "statement": "NetworkService caches OkHttpClient instances by endpoint.",
            "conditions": conds,
            "anchors": [{
                "path": "NetworkService.kt",
                "sha256": file_digest(self.repo_a / "NetworkService.kt"),
                "symbols": ["class NetworkService"],
            }],
            "checks": [{
                "kind": "json_equals",
                "evidence_id": ev["evidence_id"],
                "pointer": "/verified",
                "expected": True,
            }],
            "dependencies": [],
            "provenance": {
                "title": "NetworkService client caching: reuse per endpoint to avoid receiver leaks",
                "kind": "source_audit",
                "acquisition_tokens": 14_000,
            },
        })
        self.store_a.accept(cand["candidate_id"], 0, None, True)

        # 4) Verified pure-file P/R/Q Solution contract on bxlconfig.json
        bxl_sha = file_digest(self.repo_a / "bxlconfig.json")
        propose_solution(self.store_a, {
            "schema": "llr.solution.v1",
            "id": "verify-bxlconfig-seal",
            "workspace": "demo/android",
            "problem": {
                "intent": "bxlconfig-check",
                "goal": "Verify bxlconfig.json baseline digest with zero model calls",
                "patterns": ["verify {target_file} baseline"],
            },
            "parameters": {
                "target_file": {"type": "string", "enum": ["bxlconfig.json"]},
            },
            "preconditions": [{
                "kind": "file_sha256",
                "path": {"input": "target_file"},
                "expected": bxl_sha,
            }],
            "steps": [{
                "id": "inspect",
                "op": "inspect_file",
                "path": {"input": "target_file"},
            }],
            "postconditions": [{
                "step": "inspect",
                "pointer": "/sha256",
                "expected": bxl_sha,
            }],
            "result": {"verified_sha": {"step": "inspect", "pointer": "/sha256"}},
            "boundaries": ["Only attests bxlconfig.json file digest"],
        }, expected_version=0, approve=True)
        execute_solution(
            self.store_a, "demo/android", "verify-bxlconfig-seal", "train-a",
            "case-bxl", {"target_file": "bxlconfig.json"}, {},
            verify_candidate=True, approve=True,
        )

        # 5) Solution invoking a machine-local runner (must downgrade to candidate on Mac B!)
        runner_script = self.root / "mac_a_runner.py"
        runner_script.write_text('print(\'{"ok": true}\')\n', encoding="utf-8")
        register_runner(self.store_a, "demo/android", "local-check", {
            "argv": [sys.executable, str(runner_script)],
            "conditions": {"artifact_sha256": bxl_sha},
            "checks": [{"pointer": "/ok", "expected": True}],
            "title": "Local runner check",
            "anchors": [],
        }, approve=True)
        runners_map = json.loads((self.store_a.home / "runners.json").read_text(encoding="utf-8"))
        approval_sha = digest(canonical(runners_map["demo/android:local-check"]).encode())
        propose_solution(self.store_a, {
            "schema": "llr.solution.v1",
            "id": "runner-backed-sol",
            "workspace": "demo/android",
            "problem": {
                "intent": "runner-sol",
                "goal": "Run local-check script",
                "patterns": ["run local script check"],
            },
            "parameters": {},
            "preconditions": [{"kind": "file_sha256", "path": "bxlconfig.json", "expected": bxl_sha}],
            "steps": [{
                "id": "run1",
                "op": "run_validator",
                "runner": "local-check",
                "approval_sha256": approval_sha,
            }],
            "postconditions": [{"step": "run1", "pointer": "/verdict", "expected": "PASS"}],
            "result": {"done": True},
            "boundaries": ["Requires local runner"],
        }, expected_version=0, approve=True)

        # 6) Sharded L1 Theme
        define_theme(self.store_a, "demo/android", {
            "id": "networking",
            "title": "Emulator DNS & OkHttp networking",
            "summary": "DNS deadlocks and NetworkService caching",
            "aliases": ["dns", "emulator", "network"],
            "members": ["memo:demo/android:emulator-dns-deadlock", "claim:network-service-singleton-claim"],
            "related": [],
            "gaps": [],
        }, approve=True, expected_version=0)

    def test_cross_mac_export_inspect_import_reattestation_and_zero_model_reuse(self):
        self._seed_mac_a_doctrine()
        bundle_file = self.root / "airdrop" / "mac-a-doctrine.noomesh"

        exp = export_bundle(
            self.store_a, workspace="demo/android", output=bundle_file,
            machine_name="MacStudio-A", approve=True,
        )
        self.assertEqual(exp["status"], "BUNDLE_EXPORTED")
        self.assertTrue(bundle_file.is_file())
        self.assertEqual(exp["manifest"]["counts"]["memos"], 2)
        self.assertEqual(exp["manifest"]["counts"]["claims"], 1)
        self.assertEqual(exp["manifest"]["counts"]["solutions"], 2)

        inspected = inspect_bundle(bundle_file)
        self.assertEqual(inspected["status"], "BUNDLE_VALID")
        self.assertEqual(inspected["manifest"]["origin_machine"], "MacStudio-A")

        # Import onto Mac B!
        imp = import_bundle(self.store_b, bundle_file, approve=True)
        self.assertEqual(imp["status"], "BUNDLE_IMPORTED")
        self.assertEqual(imp["counts"]["imported"], 5)
        self.assertEqual(imp["counts"]["conflicts"], 0)
        # NetworkService.kt memo + claim -> SOFT_DRIFT (2)
        # FeatureOnlyOnBranchA.kt memo -> BRANCH_MISMATCH (1)
        # bxlconfig.json pure-file solution -> APPLICABLE (1)
        # runner-backed-sol -> RUNNER_REAPPROVAL_REQUIRED (1)
        self.assertEqual(imp["counts"]["soft_drift"], 2)
        self.assertEqual(imp["counts"]["branch_mismatch"], 1)
        self.assertEqual(imp["counts"]["applicable"], 1)
        self.assertEqual(imp["counts"]["runner_reapproval_needed"], 1)
        self.assertGreaterEqual(imp["counts"]["inherited_acquisition_tokens"], 47_000)

        # Importing the exact same bundle a second time on Mac B is 100% idempotent NOOP!
        imp_again = import_bundle(self.store_b, bundle_file, approve=True)
        self.assertEqual(imp_again["counts"]["imported"], 0)
        self.assertEqual(imp_again["counts"]["noop"], 5)

        # On Mac B, before any session hits knowledge, Gas Gauge saved tokens starts at 0.
        snap_before = menubar_snapshot(self.store_b)
        self.assertEqual(snap_before["gas_gauge"]["total_saved_tokens"], 0)

        # Now Mac B encounters the emulator DNS issue: search finds the SOFT_DRIFT memo,
        # while the missing-file Branch A memo stays safely BLOCKED/dormant!
        hit_b = search(
            self.store_b, self.repo_b,
            "Android emulator DNS no network",
            "mac-b-sess-1",
            actual={"platform": "android", "artifact_sha256": file_digest(self.repo_a / "NetworkService.kt")},
        )
        self.assertEqual(hit_b["status"], "FOUND")
        dns_match = next(m for m in hit_b["matches"] if m["id"] == "emulator-dns-deadlock")
        self.assertEqual(dns_match["reuse"], "SOFT_DRIFT")

        # Mac B's Gas Gauge immediately credits the ~25K inherited acquisition savings!
        snap_after = menubar_snapshot(self.store_b)
        self.assertGreaterEqual(snap_after["gas_gauge"]["total_saved_tokens"], 24_000)

        # Pure-file P/R/Q contract imported from Mac A executes on Mac B with 0 model calls!
        routed = route(
            self.store_b, self.repo_b,
            "verify bxlconfig.json baseline",
            "mac-b-sess-2",
        )
        self.assertEqual(routed["status"], "LOCAL_READY")
        solved = execute_solution(
            self.store_b, "demo/android", "verify-bxlconfig-seal",
            "mac-b-sess-2", "inv-1", {"target_file": "bxlconfig.json"}, {},
        )
        self.assertEqual(solved["status"], "LOCAL_SOLVED")
        self.assertEqual(solved["model_calls"], 0)

        # Meanwhile, the external-runner solution was safely downgraded to 'candidate' on Mac B!
        runner_route = route(
            self.store_b, self.repo_b,
            "run local script check",
            "mac-b-sess-2",
        )
        self.assertNotEqual(runner_route["status"], "LOCAL_READY")

    def test_lineage_fast_forward_and_divergent_conflict_detection(self):
        self._seed_mac_a_doctrine()
        bundle_v1 = self.root / "v1.noomesh"
        export_bundle(self.store_a, workspace="demo/android", output=bundle_v1, approve=True)
        import_bundle(self.store_b, bundle_v1, approve=True)

        # Mac A evolves emulator-dns-deadlock to v2 -> Mac B should cleanly FAST_FORWARD!
        propose_memo(self.store_a, {
            "schema": "llr.memo.v1",
            "id": "emulator-dns-deadlock",
            "workspace": "demo/android",
            "kind": "trap",
            "title": "Android emulator no network: stale netsimd and private DNS deadlock",
            "when": "Switching host Wi-Fi networks leaves Android emulator offline",
            "rule": "v2 updated rule: turn private_dns_mode off, stop netsimd, and pass -dns-server.",
            "trap": "Orphan netsimd keeps forwarding to stale gateway.",
            "tags": ["emulator", "dns", "netsimd"],
            "acquisition_tokens": 25_000,
            "verification": {
                "method": "incident_fix",
                "summary": "Verified v2 across Wi-Fi transitions",
                "anchors": [{
                    "path": "NetworkService.kt",
                    "sha256": file_digest(self.repo_a / "NetworkService.kt"),
                    "symbols": ["class NetworkService", "fun connect"],
                }],
            },
        }, expected_version=1, approve=True)

        bundle_v2 = self.root / "v2.noomesh"
        export_bundle(self.store_a, workspace="demo/android", output=bundle_v2, approve=True)
        ff_res = import_bundle(self.store_b, bundle_v2, approve=True)
        self.assertEqual(ff_res["counts"]["fast_forwarded"], 1)
        self.assertEqual(ff_res["counts"]["conflicts"], 0)

        # Now Mac B edits emulator-dns-deadlock independently while Mac A also edits it to a different v3!
        propose_memo(self.store_b, {
            "schema": "llr.memo.v1",
            "id": "emulator-dns-deadlock",
            "workspace": "demo/android",
            "kind": "trap",
            "title": "Android emulator no network: Mac B divergent local variant",
            "when": "Switching host Wi-Fi on Mac B",
            "rule": "Mac B custom rule branch.",
            "trap": "Orphan netsimd.",
            "tags": ["emulator", "dns"],
            "verification": {
                "method": "incident_fix",
                "summary": "Mac B local edit",
                "anchors": [{
                    "path": "NetworkService.kt",
                    "sha256": file_digest(self.repo_b / "NetworkService.kt"),
                    "symbols": ["class NetworkService"],
                }],
            },
        }, expected_version=2, approve=True)

        propose_memo(self.store_a, {
            "schema": "llr.memo.v1",
            "id": "emulator-dns-deadlock",
            "workspace": "demo/android",
            "kind": "trap",
            "title": "Android emulator no network: Mac A divergent v3 variant",
            "when": "Switching host Wi-Fi on Mac A",
            "rule": "Mac A divergent v3 rule.",
            "trap": "Orphan netsimd.",
            "tags": ["emulator", "dns"],
            "verification": {
                "method": "incident_fix",
                "summary": "Mac A v3 edit",
                "anchors": [{
                    "path": "NetworkService.kt",
                    "sha256": file_digest(self.repo_a / "NetworkService.kt"),
                    "symbols": ["class NetworkService"],
                }],
            },
        }, expected_version=2, approve=True)

        bundle_v3_diverged = self.root / "v3-diverged.noomesh"
        export_bundle(self.store_a, workspace="demo/android", output=bundle_v3_diverged, approve=True)
        div_res = import_bundle(self.store_b, bundle_v3_diverged, approve=True)
        self.assertEqual(div_res["counts"]["conflicts"], 1)

        # Mac B's local edit is preserved (not silently overwritten) and conflict is recorded!
        local_memo_b = json.loads(
            self.store_b.db.execute(
                "SELECT body FROM memos WHERE workspace='demo/android' AND id='emulator-dns-deadlock'"
            ).fetchone()[0]
        )
        self.assertIn("Mac B custom rule branch", local_memo_b["rule"])
        conflicts = list_conflicts(self.store_b, workspace="demo/android")
        self.assertEqual(conflicts["pending_conflicts"], 1)
        self.assertEqual(conflicts["conflicts"][0]["subject"], "emulator-dns-deadlock")

    def test_tampered_or_path_traversing_bundle_is_rejected_before_import(self):
        self._seed_mac_a_doctrine()
        valid_bundle = self.root / "valid.noomesh"
        export_bundle(self.store_a, workspace="demo/android", output=valid_bundle, approve=True)

        # Tamper with doctrine/memos.json inside the archive without updating manifest SHA-256
        tampered = self.root / "tampered.noomesh"
        with tarfile.open(valid_bundle, "r:gz") as src_tf, tarfile.open(tampered, "w:gz") as dst_tf:
            for m in src_tf.getmembers():
                raw = src_tf.extractfile(m).read()
                if m.name == "doctrine/memos.json":
                    raw = b"[]"
                info = tarfile.TarInfo(name=m.name)
                info.size = len(raw)
                dst_tf.addfile(info, io.BytesIO(raw))

        with self.assertRaises(RuntimeErrorBase):
            inspect_bundle(tampered)
        with self.assertRaises(RuntimeErrorBase):
            import_bundle(self.store_b, tampered, approve=True)

        # Path traversal entry (../evil) is rejected immediately
        evil = self.root / "evil.noomesh"
        with tarfile.open(evil, "w:gz") as tf:
            raw = b"{}"
            info = tarfile.TarInfo(name="../evil.json")
            info.size = len(raw)
            tf.addfile(info, io.BytesIO(raw))
        with self.assertRaises(RuntimeErrorBase):
            inspect_bundle(evil)


if __name__ == "__main__":
    unittest.main()
