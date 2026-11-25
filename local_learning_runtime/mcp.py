"""Small stdio MCP retrieval adapter, no network or write-promotion tools."""

import json
import sys
import time
from pathlib import Path
from typing import Any, Dict

from .core import Store, RuntimeErrorBase, canonical, require
from .learning import search, status, run_registered
from .discovery import discover
from .context import packet
from .dream import dream_pass
from .relay import send_interactive_relay, wait_for_interactive_run
from .solutions import route, execute_solution
from .lifecycle import maintenance
from . import __version__

TOOLS = [
    {"name": "local_learning_maintenance",
     "description": "Explain aging evidence, observed usage and maintenance priorities. "
                    "Read-only recommendations; never runs App tests, deletes data or starts learning.",
     "inputSchema": {"type": "object", "properties": {"workspace": {"type": "string"},
                     "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                     "cursor": {"type": "integer", "minimum": 0}},
                     "required": ["workspace"], "additionalProperties": False}},
    {"name": "local_learning_dream",
     "description": "Deterministic read-only offline consolidation report: symbol-anchor drift "
                    "candidates, broken anchors, L1 theme dead-link/bloat checks, and recent "
                    "fail->pass turnaround episodes. Never calls a model or mutates state.",
     "inputSchema": {"type": "object", "properties": {"workspace": {"type": "string"}},
                     "required": ["workspace"], "additionalProperties": False}},
    {"name": "local_experience_context",
     "description": "Progressive shared memory: L0 project catalog, L1 theme map, L2 task summaries "
                    "and L3 pinned detail. Bounded bytes and explicit omissions; no whole-memory injection.",
     "inputSchema": {"type": "object", "properties": {
         "cwd": {"type": "string"}, "query": {"type": "string"}, "session": {"type": "string"},
         "level": {"type": "integer", "enum": [0, 1, 2, 3]}, "node": {"type": "string"},
         "revision": {"type": "string"}, "cursor": {"type": "integer"}},
         "required": ["cwd", "query", "session"], "additionalProperties": False}},
    {"name": "local_experience_route",
     "description": "Determine whether an exact approved problem contract can be solved locally. "
                    "Similarity does not authorize execution; missing conditions are UNKNOWN.",
     "inputSchema": {"type": "object", "properties": {
         "cwd": {"type": "string"}, "query": {"type": "string"}, "session": {"type": "string"},
         "parameters": {"type": "object"}, "conditions": {"type": "object"}},
         "required": ["cwd", "query", "session"], "additionalProperties": False}},
    {"name": "local_experience_execute",
     "description": "Execute a verified, previously approved complete solution contract with "
                    "P/R/Q checks. No new commands or model planning; unverified parameter cases refused.",
     "inputSchema": {"type": "object", "properties": {
         "workspace": {"type": "string"}, "solution": {"type": "string"}, "session": {"type": "string"},
         "invocation": {"type": "string"}, "parameters": {"type": "object"},
         "conditions": {"type": "object"}},
         "required": ["workspace", "solution", "session", "invocation", "parameters", "conditions"],
         "additionalProperties": False}},
    {"name": "local_learning_search",
     "description": "Find relevant private local project knowledge before planning. Identify workspace "
                    "from actual cwd. Return bounded scoped summaries, conditions, revision and evidence "
                    "IDs. Runtime is published-observation authority; Userbot is legacy background.",
     "inputSchema": {"type": "object", "properties": {
         "cwd": {"type": "string"}, "query": {"type": "string"},
         "session": {"type": "string"}, "conditions": {"type": "object"}},
         "required": ["cwd", "query", "session"], "additionalProperties": False}},
    {"name": "local_learning_validators",
     "description": "Discover preauthorized local App runtime validators for a task. Returns only "
                    "names and scopes, never runs an App or adds authorization.",
     "inputSchema": {"type": "object", "properties": {
         "cwd": {"type": "string"}, "session": {"type": "string"},
         "platform": {"type": "string", "enum": ["android", "ios"]}},
         "required": ["cwd", "session"], "additionalProperties": False}},
    {"name": "local_learning_validate",
     "description": "Execute one previously user-approved bounded local validator. Automatically "
                    "captures minimized evidence, verifies predicates, publishes under local policy "
                    "and updates the graph in the same workflow. No extra save request needed. "
                    "Cannot register validators or expand their action scope. Include actual session/task.",
     "inputSchema": {"type": "object", "properties": {
         "cwd": {"type": "string"}, "platform": {"type": "string", "enum": ["android", "ios"]},
         "runner": {"type": "string"}, "session": {"type": "string"},
         "task": {"type": "string"}, "milestone": {"type": "string"}},
         "required": ["cwd", "runner", "session", "task", "milestone"],
         "additionalProperties": False}},
    {"name": "local_learning_status",
     "description": "Explain task capture, validation, publication and missing evidence.",
     "inputSchema": {"type": "object", "properties": {"task_run": {"type": "string"}},
                     "additionalProperties": False}},
    {"name": "local_learning_ask_choice",
     "description": "Send an interactive Adaptive Card with multiple-choice radio buttons and a "
                    "free-text reply box to the user's Microsoft Teams Workflows private chat, and "
                    "wait for their mobile decision (works across Codex and VS Code on multiple Macs).",
     "inputSchema": {"type": "object", "properties": {
         "question": {"type": "string"},
         "options": {"type": "array", "minItems": 2, "maxItems": 6,
                     "items": {"type": "object", "properties": {
                         "label": {"type": "string"}, "value": {"type": "string"}},
                         "required": ["label", "value"], "additionalProperties": False}},
         "session": {"type": "string"},
         "client": {"type": "string"},
         "timeout_seconds": {"type": "integer", "minimum": 5, "maximum": 3600}},
         "required": ["question", "options", "session"], "additionalProperties": False}}
]


def respond(store: Store, message: Dict[str, Any]) -> Dict[str, Any]:
    method = message.get("method")
    if method == "initialize":
        return {"protocolVersion": message.get("params", {}).get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "local-learning-runtime", "version": __version__}}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        params = message["params"]
        args = params.get("arguments", {})
        if params["name"] == "local_learning_search":
            result = search(store, Path(args["cwd"]), args["query"], args["session"],
                            args.get("conditions"))
        elif params["name"] == "local_learning_maintenance":
            result = maintenance(store, args["workspace"], args.get("limit", 50), args.get("cursor", 0))
        elif params["name"] == "local_learning_dream":
            result = dream_pass(store, args["workspace"], apply=False, approve=False)
        elif params["name"] == "local_experience_context":
            raw_level = args.get("level", 2)
            if isinstance(raw_level, str) and raw_level.strip().isdigit():
                raw_level = int(raw_level.strip())
            raw_cursor = args.get("cursor", 0)
            if isinstance(raw_cursor, str) and raw_cursor.strip().isdigit():
                raw_cursor = int(raw_cursor.strip())
            result = packet(store, Path(args["cwd"]), args["query"], args["session"],
                            raw_level, args.get("node"), args.get("revision"),
                            raw_cursor)
        elif params["name"] == "local_experience_route":
            result = route(store, Path(args["cwd"]), args["query"], args["session"],
                           args.get("parameters"), args.get("conditions"))
        elif params["name"] == "local_experience_execute":
            import re
            raw_inv = str(args["invocation"]).strip()
            normalized_inv = re.sub(r"\s+", "-", raw_inv)
            result = execute_solution(store, args["workspace"], args["solution"], args["session"],
                                      normalized_inv, args["parameters"], args["conditions"])
        elif params["name"] == "local_learning_status":
            result = status(store, args.get("task_run"))
        elif params["name"] == "local_learning_ask_choice":
            client_tag = str(args.get("client") or "Agent")[:24]
            sent = send_interactive_relay(
                store, str(args["session"]),
                f"🤔 [{client_tag}] 当前任务需要你做选择",
                str(args["question"]), args["options"],
                update_message="✅ 选择已发回当前 Session！",
                force=True,
            )
            result = wait_for_interactive_run(
                store, sent["run_id"],
                timeout_seconds=int(args.get("timeout_seconds", 900)),
                poll_interval=0.2,
            )
        elif params["name"] in ("local_learning_validators", "local_learning_validate"):
            found = discover(store, Path(args["cwd"]), args["session"], args.get("platform"))
            require(found["status"] == "DISCOVERED", "Validator workspace discovery: " + found["status"])
            workspace = found["workspace"]
            if params["name"] == "local_learning_validate":
                result = run_registered(store, workspace, args["runner"], args["session"],
                                        args["task"], args["milestone"])
            else:
                path = store.home / "runners.json"
                records = json.loads(path.read_text()) if path.exists() else {}
                result = {"workspace": workspace, "validators": [
                    {"runner": name[len(workspace) + 1:], "title": item["title"],
                     "scope": "Exact preapproved executable and checks; at most 30 seconds"}
                    for name, item in records.items() if name.startswith(workspace + ":")][:16]}
        else:
            raise RuntimeErrorBase("Unknown MCP tool")
        return {"content": [{"type": "text", "text": canonical(result)}],
                "isError": bool(result.get("error"))}
    raise RuntimeErrorBase("Unsupported MCP method")


def serve(store: Store) -> None:
    while True:
        line = sys.stdin.buffer.readline(256_001)
        if not line:
            return
        message = None
        try:
            require(len(line) <= 256_000, "MCP request too large")
            message = json.loads(line)
            if "id" not in message:
                continue
            result = respond(store, message)
            output = {"jsonrpc": "2.0", "id": message["id"], "result": result}
        except (RuntimeErrorBase, OSError, ValueError, KeyError, TypeError) as exc:
            output = {"jsonrpc": "2.0", "id": message.get("id") if isinstance(message, dict) else None,
                      "error": {"code": -32602, "message": str(exc)}}
        sys.stdout.write(canonical(output) + "\n")
        sys.stdout.flush()


def serve_bridge(home: Path) -> None:
    """Each client owns a stdio bridge, not its own memory service."""
    from .host import call
    while True:
        line = sys.stdin.buffer.readline(256_001)
        if not line:
            return
        message = None
        try:
            require(len(line) <= 256_000, "MCP request too large")
            message = json.loads(line)
            if "id" not in message:
                continue
            if (message.get("method") == "tools/call"
                    and message.get("params", {}).get("name") == "local_learning_ask_choice"):
                args = message["params"].get("arguments", {})
                client_tag = str(args.get("client") or "Agent")[:24]
                sent = call(home, "command", {
                    "argv": ["--home", str(home), "relay-send", "--session", str(args["session"])],
                    "relay_input": {
                        "heading": f"🤔 [{client_tag}] 当前任务需要你做选择",
                        "summary": str(args["question"]),
                        "choices": args["options"],
                        "update_message": "✅ 选择已发回当前 Session！",
                        "force": True,
                    },
                })
                deadline = time.monotonic() + min(max(int(args.get("timeout_seconds", 900)), 5), 3600)
                polled = {"status": "TIMEOUT", "run_id": sent["run_id"]}
                while time.monotonic() < deadline:
                    time.sleep(min(2.0, max(0.2, deadline - time.monotonic())))
                    polled = call(home, "command", {
                        "argv": ["--home", str(home), "relay-poll", "--run-id", sent["run_id"]]
                    })
                    if polled.get("status") != "RUNNING":
                        break
                if polled.get("status") == "TIMEOUT":
                    call(home, "command", {
                        "argv": ["--home", str(home), "relay-cancel", "--run-id", sent["run_id"]]
                    })
                result = {"content": [{"type": "text", "text": canonical(polled)}],
                          "isError": polled.get("status") != "REPLIED"}
            else:
                result = call(home, "mcp", message)
            output = {"jsonrpc": "2.0", "id": message["id"], "result": result}
        except (RuntimeErrorBase, OSError, ValueError, KeyError, TypeError) as exc:
            output = {"jsonrpc": "2.0", "id": message.get("id") if isinstance(message, dict) else None,
                      "error": {"code": -32602, "message": str(exc)}}
        sys.stdout.write(canonical(output) + "\n")
        sys.stdout.flush()
