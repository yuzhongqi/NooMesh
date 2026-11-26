"""Client-neutral JSON command interface."""

import argparse
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Optional, Sequence

from .core import (MAX_EVIDENCE_BYTES, RuntimeErrorBase, Store, file_digest,
                   require, verify_anchor, workspace_file)
from .devices import android_capture, ios_capture, run_android_recipe
from .dream import dream_pass
from .knowledge import catalog, export_portal, import_graph
from .acceptance import android_acceptance
from .discovery import discover, register_alias
from .learning import (authorize, ingest, status, graph, search, register_runner, run_registered,
                       import_debugger_log, forget_workspace, prune_noise)
from .learning import retry_milestone
from .clients import (hook, install_copilot, install_codex, install_codex_hooks,
                      codex_notify, setup_client, doctor)
from .mcp import serve
from .inbox import (claim_proactive_message, configure_inbox, inbox_status,
                    poll_proactive_inbox, register_live_session)
from .menubar import (menubar_hits_detail, menubar_item_detail, menubar_snapshot,
                      open_live_portal, probe_menubar_running, start_menubar, stop_menubar)
from .memos import invalidate_memo, propose_memo
from .notifier import configure_notifier, notifier_status, send_test_notification
from .probes import android_node
from .relay import (cancel_interactive_run, configure_relay, poll_interactive_run,
                    relay_status, send_interactive_relay, set_codex_remote_turn)
from .solutions import propose_solution, execute_solution, route, invalidate_solution
from .context import packet, define_theme, refresh_context
from .missions import create_mission, mission_status, control, tick
from .preferences import save_preference
from .lifecycle import configure as configure_memory, assess as memory_health, maintenance


def read_json(path: str) -> Any:
    file = Path(path)
    require(file.stat().st_size <= 256_000, "JSON input exceeds 256000 bytes")
    return json.loads(file.read_text(encoding="utf-8"))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Offline evidence-backed learning runtime")
    result.add_argument("--home", type=Path,
                        default=Path(os.environ.get("LLR_HOME", "~/.local-learning-runtime")).expanduser())
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    memory_policy = commands.add_parser("memory-policy")
    memory_policy.add_argument("spec")
    memory_policy.add_argument("--workspace", required=True)
    memory_policy.add_argument("--kind", choices=["claim", "solution", "memo"], required=True)
    memory_policy.add_argument("--id", required=True)
    memory_policy.add_argument("--approve", action="store_true")
    memory = commands.add_parser("memory-health")
    memory.add_argument("--workspace", required=True)
    memory.add_argument("--kind", choices=["claim", "solution", "memo"], required=True)
    memory.add_argument("--id", required=True)
    memory.add_argument("--case-key")
    maintenance_parser = commands.add_parser("maintenance")
    maintenance_parser.add_argument("--workspace", required=True)
    maintenance_parser.add_argument("--limit", type=int, default=50)
    maintenance_parser.add_argument("--cursor", type=int, default=0)
    dream_parser = commands.add_parser("dream-pass")
    dream_parser.add_argument("--workspace", required=True)
    dream_parser.add_argument("--apply", action="store_true")
    dream_parser.add_argument("--approve", action="store_true")
    setup = commands.add_parser("setup")
    setup.add_argument("workspace")
    setup.add_argument("--root", type=Path, required=True)
    setup.add_argument("--platform", choices=["android", "ios", "generic"], required=True)
    setup.add_argument("--client", choices=["copilot", "codex", "codex-hooks"], required=True)
    setup.add_argument("--profile", type=Path)
    setup.add_argument("--local-first-readonly", action="store_true")
    setup.add_argument("--approve", action="store_true")
    commands.add_parser("host-serve")
    commands.add_parser("host-status")
    commands.add_parser("host-stop")
    commands.add_parser("doctor")
    notify_cfg = commands.add_parser("notify-config")
    notify_cfg.add_argument("--webhook-file", type=Path)
    notify_cfg.add_argument("--webhook-stdin", action="store_true")
    notify_cfg.add_argument("--min-duration", type=int)
    notify_cfg.add_argument("--enable", dest="enabled", action="store_true", default=None)
    notify_cfg.add_argument("--disable", dest="enabled", action="store_false")
    notify_cfg.add_argument("--include-unregistered", dest="include_unregistered",
                            action="store_true", default=None)
    notify_cfg.add_argument("--registered-only", dest="include_unregistered",
                            action="store_false")
    notify_cfg.add_argument("--away-only", dest="away_only", action="store_true", default=None)
    notify_cfg.add_argument("--always-send", dest="away_only", action="store_false")
    notify_cfg.add_argument("--away-idle", type=int)
    notify_cfg.add_argument("--machine-name")
    notify_cfg.add_argument("--approve", action="store_true")
    commands.add_parser("notify-status")
    notify_test = commands.add_parser("notify-test")
    notify_test.add_argument("--approve", action="store_true")
    relay_cfg = commands.add_parser("relay-config")
    relay_cfg.add_argument("--webhook-stdin", action="store_true")
    relay_cfg.add_argument("--env-id")
    relay_cfg.add_argument("--flow-id")
    relay_cfg.add_argument("--min-duration", type=int)
    relay_cfg.add_argument("--poll-timeout", type=int)
    relay_cfg.add_argument("--away-only", dest="away_only", action="store_true", default=None)
    relay_cfg.add_argument("--always-send", dest="away_only", action="store_false")
    relay_cfg.add_argument("--away-idle", type=int)
    relay_cfg.add_argument("--machine-name")
    relay_cfg.add_argument("--enable", dest="enabled", action="store_true", default=None)
    relay_cfg.add_argument("--disable", dest="enabled", action="store_false")
    relay_cfg.add_argument("--auto-on-stop", dest="auto_on_stop", action="store_true", default=None)
    relay_cfg.add_argument("--manual-only", dest="auto_on_stop", action="store_false")
    relay_cfg.add_argument("--approve", action="store_true")
    commands.add_parser("relay-status")
    relay_reg = commands.add_parser("relay-register")
    relay_reg.add_argument("--session", required=True)
    relay_mark = commands.add_parser("relay-mark-codex")
    relay_mark.add_argument("--session", required=True)
    relay_mark.add_argument("--active", action="store_true")
    relay_send_cmd = commands.add_parser("relay-send")
    relay_send_cmd.add_argument("--session", required=True)
    relay_poll_cmd = commands.add_parser("relay-poll")
    relay_poll_cmd.add_argument("--run-id", required=True)
    relay_cancel_cmd = commands.add_parser("relay-cancel")
    relay_cancel_cmd.add_argument("--session")
    relay_cancel_cmd.add_argument("--run-id")
    inbox_cfg = commands.add_parser("inbox-config")
    inbox_cfg.add_argument("--reader-stdin", action="store_true")
    inbox_cfg.add_argument("--poll-seconds", type=int)
    inbox_cfg.add_argument("--default-cwd")
    inbox_cfg.add_argument("--machine-name")
    inbox_cfg.add_argument("--new-session-client", choices=["copilot", "codex"])
    inbox_cfg.add_argument("--allow-new-sessions", dest="allow_new_sessions",
                           action="store_true", default=None)
    inbox_cfg.add_argument("--no-new-sessions", dest="allow_new_sessions",
                           action="store_false")
    inbox_cfg.add_argument("--require-machine-tag", dest="require_machine_tag",
                           action="store_true", default=None)
    inbox_cfg.add_argument("--no-require-machine-tag", dest="require_machine_tag",
                           action="store_false")
    inbox_cfg.add_argument("--enable", dest="enabled", action="store_true", default=None)
    inbox_cfg.add_argument("--disable", dest="enabled", action="store_false")
    inbox_cfg.add_argument("--approve", action="store_true")
    commands.add_parser("inbox-status")
    commands.add_parser("inbox-poll")
    inbox_claim_cmd = commands.add_parser("inbox-claim")
    inbox_claim_cmd.add_argument("--session", required=True)
    inbox_hb = commands.add_parser("inbox-heartbeat")
    inbox_hb.add_argument("--session", required=True)
    inbox_hb.add_argument("--client", default="copilot")
    inbox_hb.add_argument("--cwd", default="")
    inbox_hb.add_argument("--title", default="")
    inbox_hb.add_argument("--state", default="idle")
    inbox_hb.add_argument("--heartbeat-only", action="store_true")
    inbox_hb.add_argument("--unregister", action="store_true")
    preference = commands.add_parser("preference")
    preference.add_argument("id")
    preference.add_argument("--text", required=True)
    preference.add_argument("--kind", choices=["explicit", "inferred"], required=True)
    preference.add_argument("--expected-version", type=int, required=True)
    preference.add_argument("--workspace")
    preference.add_argument("--approve", action="store_true")
    context = commands.add_parser("context")
    context.add_argument("--cwd", type=Path, default=Path.cwd())
    context.add_argument("--query", default="")
    context.add_argument("--session", required=True)
    context.add_argument("--level", type=int, choices=[0, 1, 2, 3], default=2)
    context.add_argument("--node")
    context.add_argument("--revision")
    context.add_argument("--cursor", type=int, default=0)
    context.add_argument("--budget", type=int, default=6000)
    context_refresh = commands.add_parser("context-refresh")
    context_refresh.add_argument("--session", required=True)
    theme = commands.add_parser("define-theme")
    theme.add_argument("spec")
    theme.add_argument("--workspace", required=True)
    theme.add_argument("--expected-version", type=int)
    theme.add_argument("--strict-links", action="store_true")
    theme.add_argument("--approve", action="store_true")
    memo_prop = commands.add_parser("memo-propose")
    memo_prop.add_argument("spec")
    memo_prop.add_argument("--expected-version", type=int, required=True)
    memo_prop.add_argument("--session", default="author")
    memo_prop.add_argument("--approve", action="store_true")
    memo_inv = commands.add_parser("memo-invalidate")
    memo_inv.add_argument("memo")
    memo_inv.add_argument("--workspace", required=True)
    memo_inv.add_argument("--version", type=int, required=True)
    memo_inv.add_argument("--state", choices=["stale", "conflicted", "superseded"], required=True)
    memo_inv.add_argument("--reason", required=True)
    memo_inv.add_argument("--approve", action="store_true")
    proposal = commands.add_parser("solution-propose")
    proposal.add_argument("spec")
    proposal.add_argument("--expected-version", type=int, required=True)
    proposal.add_argument("--approve", action="store_true")
    router = commands.add_parser("route")
    router.add_argument("--cwd", type=Path, default=Path.cwd())
    router.add_argument("--query", required=True)
    router.add_argument("--session", required=True)
    router.add_argument("--parameters")
    router.add_argument("--conditions")
    for name in ("solution-verify", "solution-execute"):
        execution = commands.add_parser(name)
        execution.add_argument("solution")
        execution.add_argument("--workspace", required=True)
        execution.add_argument("--session", required=True)
        execution.add_argument("--invocation", required=True)
        execution.add_argument("--parameters", required=True)
        execution.add_argument("--conditions", required=True)
        execution.add_argument("--approve", action="store_true")
    invalidation = commands.add_parser("solution-invalidate")
    invalidation.add_argument("solution")
    invalidation.add_argument("--workspace", required=True)
    invalidation.add_argument("--version", type=int, required=True)
    invalidation.add_argument("--state", choices=["stale", "conflicted", "superseded"], required=True)
    invalidation.add_argument("--approve", action="store_true")
    mission = commands.add_parser("mission-create")
    mission.add_argument("spec")
    for name in ("mission-control", "mission-status", "mission-tick"):
        job = commands.add_parser(name)
        job.add_argument("mission")
        if name == "mission-control":
            job.add_argument("action", choices=["approve", "start", "pause", "resume", "cancel"])
            job.add_argument("--approve", action="store_true")
    retry = commands.add_parser("retry-milestone")
    retry.add_argument("event_id")
    probe = commands.add_parser("probe-android-node")
    probe.add_argument("--device", required=True)
    probe.add_argument("--app", required=True)
    probe.add_argument("--resource-id", required=True)
    probe.add_argument("--adb", default="adb")
    probe.add_argument("--approve", action="store_true")
    alias = commands.add_parser("register-alias")
    alias.add_argument("--workspace", required=True)
    alias.add_argument("--root", type=Path, required=True)
    alias.add_argument("--require-platform", action="store_true")
    alias.add_argument("--approve", action="store_true")
    debugger = commands.add_parser("import-debugger")
    debugger.add_argument("source", type=Path)
    debugger.add_argument("--workspace", required=True)
    debugger.add_argument("--session", required=True)
    debugger.add_argument("--task", required=True)
    debugger.add_argument("--title", required=True)
    debugger.add_argument("--approve", action="store_true")
    forgotten = commands.add_parser("forget-workspace")
    forgotten.add_argument("--workspace", required=True)
    forgotten.add_argument("--approve", action="store_true")
    pruned = commands.add_parser("prune-noise")
    pruned.add_argument("--approve", action="store_true")
    pruned.add_argument("--vacuum", action="store_true")
    policy = commands.add_parser("authorize-local")
    policy.add_argument("--workspace", action="append", required=True)
    policy.add_argument("--auto-dream-source", dest="auto_dream_source",
                        action="store_true", default=None)
    policy.add_argument("--no-auto-dream-source", dest="auto_dream_source",
                        action="store_false")
    policy.add_argument("--dream-interval", type=int)
    policy.add_argument("--approve", action="store_true")
    incoming = commands.add_parser("ingest")
    incoming.add_argument("event")
    states = commands.add_parser("task-status")
    states.add_argument("--task-run")
    projection = commands.add_parser("graph")
    projection.add_argument("--workspace", required=True)
    lookup = commands.add_parser("search")
    lookup.add_argument("--cwd", type=Path, default=Path.cwd())
    lookup.add_argument("--query", required=True)
    lookup.add_argument("--session", required=True)
    lookup.add_argument("--conditions")
    registration = commands.add_parser("register-runner")
    registration.add_argument("name")
    registration.add_argument("spec")
    registration.add_argument("--workspace", required=True)
    registration.add_argument("--approve", action="store_true")
    runner = commands.add_parser("run-validator")
    runner.add_argument("name")
    runner.add_argument("--workspace", required=True)
    runner.add_argument("--session", required=True)
    runner.add_argument("--task", required=True)
    runner.add_argument("--milestone", required=True)
    lifecycle = commands.add_parser("hook")
    lifecycle.add_argument("--client", choices=["copilot", "vscode-local", "codex-hooks"], required=True)
    lifecycle.add_argument("--event", required=True)
    installation = commands.add_parser("install-client")
    installation.add_argument("client", choices=["copilot", "codex", "codex-hooks"])
    installation.add_argument("--runtime-root", type=Path, required=True)
    installation.add_argument("--profile", type=Path, required=True)
    installation.add_argument("--approve", action="store_true")
    commands.add_parser("mcp")
    notify = commands.add_parser("notify")
    notify.add_argument("payload")
    discovery = commands.add_parser("discover")
    discovery.add_argument("--cwd", type=Path, default=Path.cwd())
    discovery.add_argument("--session", required=True)
    discovery.add_argument("--platform", choices=["android", "ios", "generic"])
    register = commands.add_parser("register")
    register.add_argument("workspace")
    register.add_argument("--root", type=Path, required=True)
    register.add_argument("--platform", choices=["android", "ios", "generic"], required=True)
    anchor = commands.add_parser("anchor")
    anchor.add_argument("workspace")
    anchor.add_argument("path")
    anchor.add_argument("--symbol", action="append")
    observe = commands.add_parser("observe")
    observe.add_argument("--workspace", required=True)
    observe.add_argument("--event", required=True)
    observe.add_argument("--conditions", required=True)
    observe.add_argument("--file", type=Path, required=True)
    observe.add_argument("--type", choices=["application/json", "application/xml", "image/png"],
                         required=True)
    propose = commands.add_parser("propose")
    propose.add_argument("spec")
    for name in ("verify", "accept"):
        command = commands.add_parser(name)
        command.add_argument("candidate")
        if name == "accept":
            command.add_argument("--expected-version", type=int, required=True)
            command.add_argument("--expected-revision", required=True,
                                 help="Use 'none' only for initial publication")
            command.add_argument("--approve", action="store_true")
    recall = commands.add_parser("recall")
    recall.add_argument("task")
    recall.add_argument("--session", required=True)
    refresh = commands.add_parser("refresh")
    refresh.add_argument("--session", required=True)
    commands.add_parser("questions")
    intake = commands.add_parser("import-graph")
    intake.add_argument("source", type=Path)
    intake.add_argument("--workspace", required=True)
    intake.add_argument("--node", action="append", required=True)
    intake.add_argument("--approve", action="store_true")
    cards = commands.add_parser("catalog")
    cards.add_argument("--workspace", required=True)
    portal = commands.add_parser("export-portal")
    portal.add_argument("--workspace", required=True)
    portal.add_argument("--output", type=Path, required=True)
    commands.add_parser("menubar-snapshot")
    mb_hits = commands.add_parser("menubar-hits")
    mb_hits.add_argument("--hours", type=int, default=24)
    mb_item = commands.add_parser("menubar-item")
    mb_item.add_argument("--kind", choices=["memo", "claim", "solution"], required=True)
    mb_item.add_argument("--subject", required=True)
    mb_item.add_argument("--workspace")
    commands.add_parser("menubar-status")
    mb_start = commands.add_parser("menubar-start")
    mb_start.add_argument("--install-login-agent", action="store_true")
    mb_start.add_argument("--approve", action="store_true")
    mb_stop = commands.add_parser("menubar-stop")
    mb_stop.add_argument("--keep-autostart", action="store_true")
    mb_stop.add_argument("--uninstall-login-agent", action="store_true")
    mb_portal = commands.add_parser("menubar-open-portal")
    mb_portal.add_argument("--workspace")
    mb_portal.add_argument("--no-open", action="store_true")
    acceptance = commands.add_parser("acceptance-android")
    acceptance.add_argument("--workspace", required=True)
    acceptance.add_argument("--device", required=True)
    acceptance.add_argument("--app", required=True)
    acceptance.add_argument("--resource-id", required=True)
    acceptance.add_argument("--context", required=True)
    acceptance.add_argument("--output", type=Path, required=True)
    acceptance.add_argument("--approve", action="store_true")
    invalidate = commands.add_parser("invalidate")
    invalidate.add_argument("claim")
    invalidate.add_argument("--reason", required=True)
    invalidate.add_argument("--lifecycle", choices=["stale", "conflicted"], required=True)
    invalidate.add_argument("--expected-version", type=int, required=True)
    invalidate.add_argument("--expected-revision", required=True)
    invalidate.add_argument("--approve", action="store_true")
    recipe = commands.add_parser("run-android")
    recipe.add_argument("recipe")
    recipe.add_argument("--approve", action="store_true")
    recipe.add_argument("--session")
    recipe.add_argument("--task")
    packet = commands.add_parser("learning-packet")
    packet.add_argument("task")
    packet.add_argument("--question", required=True)
    for name in ("capture-android", "capture-ios"):
        capture = commands.add_parser(name)
        capture.add_argument("--workspace", required=True)
        capture.add_argument("--device", required=True)
        capture.add_argument("--app", required=True)
        capture.add_argument("--context", required=True)
        capture.add_argument("--approve", action="store_true")
    unlock = commands.add_parser("unlock-device")
    unlock.add_argument("device")
    unlock.add_argument("--approve", action="store_true")
    return result


def dispatch(store: Store, args: argparse.Namespace) -> Any:
    if args.command == "init":
        return {"status": "ok", "home": str(store.home), "model_calls": 0}
    if args.command == "memory-policy":
        return configure_memory(store, args.workspace, args.kind, args.id, read_json(args.spec), args.approve)
    if args.command == "memory-health":
        return memory_health(store, args.workspace, args.kind, args.id, args.case_key)
    if args.command == "maintenance":
        return maintenance(store, args.workspace, args.limit, args.cursor)
    if args.command == "dream-pass":
        return dream_pass(store, args.workspace, args.apply, args.approve)
    if args.command == "setup":
        return setup_client(store, args.workspace, args.root, args.platform, args.client,
                            args.profile, args.approve, args.local_first_readonly)
    if args.command == "doctor":
        return doctor(store)
    if args.command == "notify-config":
        url = getattr(args, "webhook_value", None)
        if url is None and args.webhook_file is not None:
            require(args.webhook_file.stat().st_size <= 8000, "Webhook file too large")
            url = args.webhook_file.read_text(encoding="utf-8").strip()
        if url is None:
            url = os.environ.get("LLR_TEAMS_WEBHOOK_URL")
        return configure_notifier(store, args.approve, url, args.min_duration,
                                  args.enabled, args.include_unregistered,
                                  args.away_only, args.away_idle, args.machine_name)
    if args.command == "notify-status":
        return notifier_status(store)
    if args.command == "notify-test":
        return send_test_notification(store, args.approve)
    if args.command == "relay-config":
        url = getattr(args, "webhook_value", None) or os.environ.get("LLR_TEAMS_RELAY_URL")
        return configure_relay(store, args.approve, url, args.env_id, args.flow_id,
                               args.enabled, args.auto_on_stop, args.min_duration,
                               args.poll_timeout, args.away_only, args.away_idle,
                               args.machine_name)
    if args.command == "relay-status":
        return relay_status(store)
    if args.command == "relay-mark-codex":
        return set_codex_remote_turn(store, args.session, args.active)
    if args.command == "relay-register":
        from .core import now
        with store.transaction():
            store.db.execute(
                "INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                ("relay_session:" + args.session[:64], now()))
        return relay_status(store)
    if args.command == "relay-send":
        body = getattr(args, "relay_payload", None)
        if body is None:
            raw = sys.stdin.buffer.read(64_001)
            require(len(raw) <= 64_000, "Relay input too large")
            body = json.loads(raw)
        return send_interactive_relay(
            store, args.session, str(body.get("heading", "")),
            str(body.get("summary", "")), body.get("choices"),
            str(body.get("update_message", "✅ 已发回电脑当前 Session！")),
            turn_source=str(body.get("turn_source", "local")),
            elapsed_seconds=body.get("elapsed_seconds"),
            force=bool(body.get("force", False)))
    if args.command == "relay-poll":
        return poll_interactive_run(store, args.run_id)
    if args.command == "relay-cancel":
        return cancel_interactive_run(store, args.session, args.run_id)
    if args.command == "inbox-config":
        url = getattr(args, "reader_value", None) or os.environ.get("LLR_TEAMS_READER_URL")
        return configure_inbox(
            store, args.approve, url, args.enabled, args.poll_seconds,
            args.allow_new_sessions, args.new_session_client, args.default_cwd,
            args.machine_name, args.require_machine_tag,
        )
    if args.command == "inbox-status":
        return inbox_status(store)
    if args.command == "inbox-poll":
        return poll_proactive_inbox(store)
    if args.command == "inbox-claim":
        return claim_proactive_message(store, args.session)
    if args.command == "inbox-heartbeat":
        if args.unregister:
            with store.transaction():
                store.db.execute("DELETE FROM live_sessions WHERE session=?", (args.session,))
            return {"session": args.session, "unregistered": True}
        register_live_session(store, args.session, args.client, args.cwd, args.title, args.state)
        if args.heartbeat_only:
            return {"status": "HEARTBEAT_OK"}
        return claim_proactive_message(store, args.session)
    if args.command == "context":
        return packet(store, args.cwd, args.query, args.session, args.level,
                      args.node, args.revision, args.cursor, args.budget)
    if args.command == "preference":
        return save_preference(store, args.id, args.text, args.kind,
                               args.expected_version, args.approve, args.workspace)
    if args.command == "context-refresh":
        return refresh_context(store, args.session)
    if args.command == "define-theme":
        return define_theme(store, args.workspace, read_json(args.spec), args.approve,
                            args.expected_version, args.strict_links)
    if args.command == "memo-propose":
        return propose_memo(store, read_json(args.spec), args.expected_version,
                            args.approve, args.session)
    if args.command == "memo-invalidate":
        return invalidate_memo(store, args.workspace, args.memo, args.version,
                               args.state, args.reason, args.approve)
    if args.command == "solution-propose":
        return propose_solution(store, read_json(args.spec), args.expected_version, args.approve)
    if args.command == "route":
        return route(store, args.cwd, args.query, args.session,
                     read_json(args.parameters) if args.parameters else None,
                     read_json(args.conditions) if args.conditions else None)
    if args.command in ("solution-verify", "solution-execute"):
        return execute_solution(store, args.workspace, args.solution, args.session,
                                args.invocation, read_json(args.parameters), read_json(args.conditions),
                                args.command == "solution-verify", args.approve)
    if args.command == "solution-invalidate":
        return invalidate_solution(store, args.workspace, args.solution, args.version, args.state, args.approve)
    if args.command == "mission-create":
        return create_mission(store, read_json(args.spec))
    if args.command == "mission-control":
        return control(store, args.mission, args.action, args.approve)
    if args.command == "mission-status":
        return mission_status(store, args.mission)
    if args.command == "mission-tick":
        return tick(store, args.mission)
    if args.command == "retry-milestone":
        return retry_milestone(store, args.event_id)
    if args.command == "probe-android-node":
        return android_node(args.device, args.app, args.resource_id, args.adb, args.approve)
    if args.command == "register-alias":
        return register_alias(store, args.workspace, args.root, args.require_platform, args.approve)
    if args.command == "import-debugger":
        return import_debugger_log(store, args.workspace, args.source, args.session, args.task,
                                   args.title, args.approve)
    if args.command == "forget-workspace":
        return forget_workspace(store, args.workspace, args.approve)
    if args.command == "prune-noise":
        return prune_noise(store, args.approve, args.vacuum)
    if args.command == "authorize-local":
        return authorize(store, args.workspace, args.approve,
                         args.auto_dream_source, args.dream_interval)
    if args.command == "ingest":
        return ingest(store, read_json(args.event))
    if args.command == "task-status":
        return status(store, args.task_run)
    if args.command == "graph":
        return graph(store, args.workspace)
    if args.command == "search":
        return search(store, args.cwd, args.query, args.session,
                      read_json(args.conditions) if args.conditions else None)
    if args.command == "register-runner":
        return register_runner(store, args.workspace, args.name, read_json(args.spec), args.approve)
    if args.command == "run-validator":
        return run_registered(store, args.workspace, args.name, args.session, args.task, args.milestone)
    if args.command == "hook":
        raw = sys.stdin.buffer.read(256_001)
        require(len(raw) <= 256_000, "Hook input too large")
        return hook(store, args.client, args.event, json.loads(raw))
    if args.command == "install-client":
        installer = {"copilot": install_copilot, "codex": install_codex,
                     "codex-hooks": install_codex_hooks}[args.client]
        return installer(store, args.runtime_root, args.profile, args.approve)
    if args.command == "notify":
        return codex_notify(store, json.loads(args.payload))
    if args.command == "discover":
        return discover(store, args.cwd, args.session, args.platform)
    if args.command == "register":
        return store.register(args.workspace, args.root, args.platform)
    if args.command == "anchor":
        scope = store.workspace(args.workspace)
        root = Path(scope["root"])
        path = workspace_file(root, args.path)
        item = {"path": args.path, "sha256": file_digest(path)}
        if args.symbol:
            item["symbols"] = list(dict.fromkeys(args.symbol))
            verify_anchor(root, item, require_exact=True)
        return item
    if args.command == "observe":
        require(args.file.stat().st_size <= MAX_EVIDENCE_BYTES, "Evidence too large")
        return store.observe(args.event, args.workspace, read_json(args.conditions),
                             args.file.read_bytes(), args.type)
    if args.command == "propose":
        return store.propose(read_json(args.spec))
    if args.command == "verify":
        return store.verify(store.candidate(args.candidate))
    if args.command == "accept":
        revision = None if args.expected_revision == "none" else args.expected_revision
        return store.accept(args.candidate, args.expected_version, revision, args.approve)
    if args.command == "recall":
        return store.recall(read_json(args.task), args.session)
    if args.command == "refresh":
        return store.refresh(args.session)
    if args.command == "questions":
        return store.questions()
    if args.command == "import-graph":
        return import_graph(store, args.workspace, args.source, args.node, args.approve)
    if args.command == "catalog":
        return catalog(store, args.workspace)
    if args.command == "export-portal":
        return export_portal(store, args.workspace, args.output)
    if args.command == "menubar-snapshot":
        return menubar_snapshot(store)
    if args.command == "menubar-hits":
        return menubar_hits_detail(store, hours=args.hours)
    if args.command == "menubar-item":
        return menubar_item_detail(store, args.kind, args.subject, workspace=args.workspace)
    if args.command == "menubar-status":
        return probe_menubar_running(store.home)
    if args.command == "menubar-start":
        return start_menubar(store.home, args.approve, install_login=args.install_login_agent)
    if args.command == "menubar-stop":
        return stop_menubar(store.home, disable_autostart=not args.keep_autostart,
                            uninstall_login=args.uninstall_login_agent)
    if args.command == "menubar-open-portal":
        return open_live_portal(store, workspace=args.workspace, open_browser=not args.no_open)
    if args.command == "acceptance-android":
        return android_acceptance(store, args.workspace, args.device, args.app, args.resource_id,
                                  read_json(args.context), args.output, args.approve)
    if args.command == "invalidate":
        return store.invalidate(args.claim, args.reason, args.lifecycle, args.expected_version,
                                args.expected_revision, args.approve)
    if args.command == "run-android":
        if args.session:
            os.environ["LLR_SESSION"] = args.session
        if args.task:
            os.environ["LLR_TASK"] = args.task
        return run_android_recipe(store, read_json(args.recipe), args.approve)
    if args.command == "learning-packet":
        return store.learning_packet(read_json(args.task), args.question)
    if args.command == "capture-android":
        return android_capture(store, args.workspace, args.device, args.app,
                               read_json(args.context), args.approve)
    if args.command == "capture-ios":
        return ios_capture(store, args.workspace, args.device, args.app,
                           read_json(args.context), args.approve)
    if args.command == "unlock-device":
        return store.unlock_device(args.device, args.approve)
    raise RuntimeErrorBase("Unknown command")


def _await_codex_relay_via_ipc(home: Path, wait_spec: dict, base_output: dict) -> dict:
    from .host import call
    run_id = wait_spec["run_id"]
    session = wait_spec["session"]
    deadline = time.monotonic() + min(max(int(wait_spec.get("timeout_seconds", 1800)), 5), 3600)
    interrupted = False

    def _on_term(*_args):
        nonlocal interrupted
        interrupted = True

    old_term = signal.signal(signal.SIGTERM, _on_term)
    try:
        while time.monotonic() < deadline and not interrupted:
            polled = call(home, "command", {
                "argv": ["--home", str(home), "relay-poll", "--run-id", run_id]
            })
            if polled.get("status") != "RUNNING":
                if polled.get("status") == "REPLIED" and polled.get("should_continue") and polled.get("prompt"):
                    call(home, "command", {
                        "argv": ["--home", str(home), "relay-mark-codex", "--session", session, "--active"]
                    })
                    return {
                        "decision": "block",
                        "reason": polled["prompt"],
                        "systemMessage": "📱 Continuing Codex session from Teams reply",
                    }
                call(home, "command", {
                    "argv": ["--home", str(home), "relay-mark-codex", "--session", session]
                })
                return base_output
            time.sleep(min(2.0, max(0.2, deadline - time.monotonic())))
    except KeyboardInterrupt:
        interrupted = True
    finally:
        signal.signal(signal.SIGTERM, old_term)
    call(home, "command", {"argv": ["--home", str(home), "relay-cancel", "--run-id", run_id]})
    call(home, "command", {"argv": ["--home", str(home), "relay-mark-codex", "--session", session]})
    return base_output


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = list(argv) if argv is not None else sys.argv[1:]
    args = parser().parse_args(arguments)
    try:
        from .host import call, serve_host, ensure_host, stop_host
        if args.command == "host-serve":
            serve_host(args.home)
            return 0
        if args.command == "host-status":
            output = ensure_host(args.home)
        elif args.command == "host-stop":
            output = stop_host(args.home)
        elif args.command == "mcp":
            from .mcp import serve_bridge
            serve_bridge(args.home)
            return 0
        elif args.command in ("menubar-snapshot", "menubar-hits", "menubar-item", "menubar-status",
                              "menubar-start", "menubar-stop", "menubar-open-portal"):
            store = Store(args.home)
            try:
                output = dispatch(store, args)
            finally:
                store.close()
        else:
            payload = {"argv": ["--home", str(args.home)] + arguments}
            if args.command == "hook":
                raw = sys.stdin.buffer.read(256_001)
                require(len(raw) <= 256_000, "Hook input too large")
                payload["hook_input"] = json.loads(raw)
            elif args.command in ("notify-config", "relay-config") and args.webhook_stdin:
                raw_url = sys.stdin.buffer.read(8001)
                require(len(raw_url) <= 8000, "Webhook URL input too large")
                payload["webhook_stdin"] = raw_url.decode("utf-8").strip()
            elif args.command == "inbox-config" and args.reader_stdin:
                raw_url = sys.stdin.buffer.read(8001)
                require(len(raw_url) <= 8000, "Reader URL input too large")
                payload["reader_stdin"] = raw_url.decode("utf-8").strip()
            elif args.command == "relay-send":
                raw_body = sys.stdin.buffer.read(64_001)
                require(len(raw_body) <= 64_000, "Relay input too large")
                payload["relay_input"] = json.loads(raw_body)
            output = call(args.home, "command", payload)
            if args.command == "hook" and isinstance(output, dict) and "_await_relay" in output:
                wait_spec = output.pop("_await_relay")
                output = _await_codex_relay_via_ipc(args.home, wait_spec, output)
        print(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False))
        if isinstance(output, dict) and output.get("error"):
            return 1
        if isinstance(output, dict) and output.get("status") in (
                "BLOCKED", "NEEDS_INVESTIGATION", "NEEDS_INPUT", "NEEDS_EXPLORATION", "FAILED", "ITEM_TOO_LARGE"):
            return 2
        if isinstance(output, dict) and output.get("status") in ("AMBIGUOUS", "UNKNOWN_WORKSPACE"):
            return 2
        if isinstance(output, dict) and output.get("verdict") == "FAIL":
            return 2
        if isinstance(output, dict) and output.get("verdict") in ("INCONCLUSIVE", "PARTIAL"):
            return 2
        return 0
    except (RuntimeErrorBase, OSError, ValueError, sqlite3.Error, ET.ParseError,
            subprocess.TimeoutExpired) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
