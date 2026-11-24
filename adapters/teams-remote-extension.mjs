// User-scoped Copilot SDK extension template: ~/.copilot/extensions/teams-remote/extension.mjs
// Bidirectional Microsoft Teams Workflows relay for continuing VS Code Copilot sessions and mobile choices

import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { joinSession } from "@github/copilot-sdk/extension";

const XCODE_PYTHON = "/Applications/Xcode_26.4.1.app/Contents/Developer/usr/bin/python3";
const PYTHON_BIN = process.env.LLR_PYTHON || (existsSync(XCODE_PYTHON) ? XCODE_PYTHON : "python3");
const LLR_SCRIPT = process.env.LLR_SCRIPT || join(homedir(), "local-learning-runtime", "tools", "llr.py");
const LLR_HOME = process.env.LLR_HOME || join(homedir(), ".local-learning-runtime");

function formatDuration(sec) {
    const total = Math.max(0, Math.round(sec));
    if (total < 60) return `${total}s`;
    const mins = Math.floor(total / 60);
    const rem = total % 60;
    if (mins < 60) return `${mins}m ${rem}s`;
    return `${Math.floor(mins / 60)}h ${mins % 60}m`;
}

function runLlr(args, stdinObj = null, timeoutMs = 20000) {
    return new Promise((resolve, reject) => {
        const child = spawn(PYTHON_BIN, [LLR_SCRIPT, "--home", LLR_HOME, ...args], {
            stdio: ["pipe", "pipe", "pipe"],
        });
        let stdout = "";
        let stderr = "";
        const timer = setTimeout(() => {
            child.kill();
            reject(new Error(`llr timeout after ${timeoutMs}ms: ${args[0]}`));
        }, timeoutMs);
        child.stdout.on("data", (chunk) => {
            stdout += chunk.toString("utf-8");
        });
        child.stderr.on("data", (chunk) => {
            stderr += chunk.toString("utf-8");
        });
        child.on("error", (err) => {
            clearTimeout(timer);
            reject(err);
        });
        child.on("close", (code) => {
            clearTimeout(timer);
            try {
                const parsed = stdout.trim() ? JSON.parse(stdout) : {};
                if (code !== 0 && parsed?.error) {
                    reject(new Error(parsed.error));
                } else if (code !== 0) {
                    reject(new Error(stderr.trim() || `llr exited with ${code}`));
                } else {
                    resolve(parsed);
                }
            } catch (err) {
                reject(new Error(`Invalid llr JSON (${code}): ${stderr || stdout}`));
            }
        });
        if (stdinObj !== null) {
            child.stdin.write(JSON.stringify(stdinObj));
        }
        child.stdin.end();
    });
}

let currentSessionId = null;
let turnStartedAt = Date.now();
let lastAssistantText = "";
let activeRunId = null;
let pollTimer = null;
let pollInFlight = false;
let injectingFromTeams = false;

function stopPolling() {
    if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
    }
    pollInFlight = false;
}

async function cancelActiveRelay() {
    stopPolling();
    if (activeRunId) {
        const rid = activeRunId;
        activeRunId = null;
        await runLlr(["relay-cancel", "--run-id", rid]).catch(() => {});
    }
}

const session = await joinSession({
    tools: [
        {
            name: "ask_teams_choice",
            description:
                "Send an interactive Adaptive Card with choices and a free-text reply box to the user's Microsoft Teams Workflows private chat, and wait for their mobile/remote decision without ending the current turn. Use when the user asked to decide via Teams or is away from the computer.",
            skipPermission: true,
            parameters: {
                type: "object",
                properties: {
                    question: {
                        type: "string",
                        description: "Clear summary of the question or decision needed.",
                    },
                    options: {
                        type: "array",
                        description: "2 to 6 choices presented as radio buttons on Teams.",
                        items: {
                            type: "object",
                            properties: {
                                label: { type: "string", description: "Human-readable button label" },
                                value: { type: "string", description: "Option identifier or instruction" },
                            },
                            required: ["label", "value"],
                        },
                    },
                    timeoutSeconds: {
                        type: "integer",
                        description: "Maximum seconds to wait for mobile response (default 900).",
                    },
                },
                required: ["question", "options"],
            },
            handler: async (args, invocation) => {
                const sid = invocation?.sessionId || currentSessionId || "vscode-session";
                currentSessionId = sid;
                await cancelActiveRelay();
                const timeoutSec = Math.min(Math.max(Number(args.timeoutSeconds) || 900, 15), 3600);
                await session
                    .log("📱 Sent interactive choice card to Teams Workflows — waiting for your mobile reply...", {
                        ephemeral: true,
                    })
                    .catch(() => {});
                const sent = await runLlr(["relay-send", "--session", sid], {
                    heading: "🤔 [VS Code] 当前任务需要你做选择",
                    summary: String(args.question || ""),
                    choices: Array.isArray(args.options) ? args.options : [],
                    update_message: "✅ 选择已发回当前 VS Code Session！",
                });
                const runId = sent.run_id;
                const deadline = Date.now() + timeoutSec * 1000;
                while (Date.now() < deadline) {
                    await new Promise((r) => setTimeout(r, 2500));
                    const polled = await runLlr(["relay-poll", "--run-id", runId]);
                    if (polled.status !== "RUNNING") {
                        return {
                            resultType: polled.status === "REPLIED" ? "success" : "failure",
                            textResultForLlm: JSON.stringify(polled),
                        };
                    }
                }
                await runLlr(["relay-cancel", "--run-id", runId]).catch(() => {});
                return {
                    resultType: "timeout",
                    textResultForLlm: JSON.stringify({ status: "TIMEOUT", run_id: runId }),
                };
            },
        },
    ],
    hooks: {
        onSessionStart: async (input, invocation) => {
            currentSessionId = invocation?.sessionId || input?.sessionId || currentSessionId;
            turnStartedAt = Date.now();
            if (currentSessionId) {
                runLlr(["relay-register", "--session", currentSessionId]).catch(() => {});
            }
        },
        onUserPromptSubmitted: async (input, invocation) => {
            currentSessionId = invocation?.sessionId || input?.sessionId || currentSessionId;
            turnStartedAt = Date.now();
            lastAssistantText = "";
            if (currentSessionId) {
                runLlr(["relay-register", "--session", currentSessionId]).catch(() => {});
            }
            if (!injectingFromTeams && activeRunId) {
                await cancelActiveRelay();
            }
            injectingFromTeams = false;
        },
        onAgentStop: async (input, invocation) => {
            const sid = invocation?.sessionId || input?.sessionId || currentSessionId || "vscode-session";
            currentSessionId = sid;
            try {
                const cfg = await runLlr(["relay-register", "--session", sid]);
                if (!cfg?.configured || !cfg?.enabled || !cfg?.auto_on_stop) return;
                const elapsedSec = Math.max(0, (Date.now() - turnStartedAt) / 1000);
                if (elapsedSec < (cfg.min_duration_seconds ?? 15)) return;
                await cancelActiveRelay();
                const cwdTail = String(input?.workingDirectory || "")
                    .split("/")
                    .filter(Boolean)
                    .slice(-2)
                    .join("/");
                const heading = `✅ [VS Code] 任务阶段完成 (${formatDuration(elapsedSec)})${cwdTail ? " · " + cwdTail : ""}`;
                const summary =
                    lastAssistantText.slice(0, 1000) ||
                    "当前轮次已执行完毕。你可以在下方输入下一条指令继续当前 Session。";
                const sent = await runLlr(["relay-send", "--session", sid], {
                    heading,
                    summary,
                    update_message: "✅ 指令已送达当前 VS Code Session，正在继续执行！",
                });
                activeRunId = sent.run_id;
                const startedPollAt = Date.now();
                const maxWaitMs = (cfg.poll_timeout_seconds || 1800) * 1000;
                pollTimer = setInterval(async () => {
                    if (pollInFlight || !activeRunId) return;
                    if (Date.now() - startedPollAt > maxWaitMs) {
                        await cancelActiveRelay();
                        return;
                    }
                    pollInFlight = true;
                    const targetRun = activeRunId;
                    try {
                        const polled = await runLlr(["relay-poll", "--run-id", targetRun]);
                        if (activeRunId !== targetRun) return;
                        if (polled.status === "RUNNING") return;
                        stopPolling();
                        activeRunId = null;
                        if (polled.status === "REPLIED" && polled.should_continue && polled.prompt) {
                            injectingFromTeams = true;
                            turnStartedAt = Date.now();
                            await session
                                .log(`📱 Teams remote reply received — continuing session...`, {
                                    ephemeral: true,
                                })
                                .catch(() => {});
                            setTimeout(() => {
                                session.send({ prompt: polled.prompt }).catch((err) => {
                                    injectingFromTeams = false;
                                    console.error("teams-remote session.send failed:", err?.message || err);
                                });
                            }, 50);
                        }
                    } catch (err) {
                        console.error("teams-remote poll error:", err?.message || err);
                    } finally {
                        pollInFlight = false;
                    }
                }, 2500);
            } catch (err) {
                console.error("teams-remote onAgentStop error:", err?.message || err);
            }
        },
        onSessionEnd: async () => {
            await cancelActiveRelay();
        },
    },
});

session.on("assistant.turn_start", () => {
    turnStartedAt = Date.now();
});

session.on("assistant.message", (event) => {
    const content = String(event?.data?.content || "").trim();
    if (content) {
        lastAssistantText = content;
    }
});
