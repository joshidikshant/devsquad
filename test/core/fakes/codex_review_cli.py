#!/usr/bin/env python3
"""Small Codex app-server fixture used by the public M3 native review tests."""

import json
import re
import sys


if "--version" in sys.argv:
    print("codex-cli 0.153.4")
    raise SystemExit(0)


model = "gpt-fake-review"
for index, argument in enumerate(sys.argv[:-1]):
    if argument == "-c":
        match = re.fullmatch(r'model="([^"]+)"', sys.argv[index + 1])
        if match:
            model = match.group(1)


initialized = False
thread_id = "fixture-thread"
turn_id = "fixture-turn"
for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    request_id = request.get("id")
    params = request.get("params", {})
    if method == "initialize":
        print(json.dumps({
            "id": request_id,
            "result": {"serverInfo": {"name": "fake-codex", "version": "0.153.4"}},
        }), flush=True)
    elif method == "initialized":
        initialized = True
    elif method == "model/list":
        if not initialized:
            print(json.dumps({
                "id": request_id,
                "error": {"code": -32002, "message": "not initialized"},
            }), flush=True)
            continue
        print(json.dumps({
            "id": request_id,
            "result": {
                "data": [{
                    "id": model,
                    "supportedReasoningEfforts": [{
                        "reasoningEffort": "low",
                        "description": "fixture",
                    }],
                }],
                "nextCursor": None,
            },
        }), flush=True)
    elif method == "thread/start":
        sandbox = (
            {"type": "workspaceWrite", "writableRoots": [params["cwd"]], "networkAccess": False}
            if model.endswith("identity-drift")
            else {"type": "readOnly", "networkAccess": False}
        )
        print(json.dumps({
            "id": request_id,
            "result": {
                "thread": {
                    "id": thread_id,
                    "cliVersion": "0.153.4",
                    "modelProvider": "openai",
                },
                "model": params["model"],
                "modelProvider": "openai",
                "reasoningEffort": "low",
                "cwd": params["cwd"],
                "sandbox": sandbox,
                "approvalPolicy": "never",
                "activePermissionProfile": None,
            },
        }), flush=True)
    elif method == "turn/start":
        if model.endswith("denied"):
            print(json.dumps({
                "id": request_id,
                "error": {"code": -32000, "message": "permission denied by fixture"},
            }), flush=True)
            continue
        prompt = params["input"][0]["text"]
        assignment = json.loads(prompt.splitlines()[-1])
        if "single read-only lead" in prompt:
            review = {
                "schema_version": 1,
                "candidate_sha256": assignment["candidate_sha256"],
                "disposition": "reject" if model.endswith("lead-reject") else "accept",
                "reason": "The frozen review and checks support this disposition.",
            }
        else:
            review = {
                "schema_version": 1,
                "candidate_sha256": assignment["candidate_sha256"],
                "base_oid": assignment["base_oid"],
                "target_oid": assignment["target_oid"],
                "review_mode": assignment["review_mode"],
                "verdict": "clean",
                "summary": "The bounded native fixture found no supported defect.",
                "findings": [],
            }
        output = (
            "" if model.endswith("empty")
            else "{}" if model.endswith("malformed")
            else json.dumps(review, sort_keys=True, separators=(",", ":"))
        )
        print(json.dumps({
            "id": request_id,
            "result": {"turn": {"id": turn_id}},
        }), flush=True)
        if model.endswith("disconnect"):
            break
        print(json.dumps({
            "method": "turn/started",
            "params": {
                "threadId": thread_id,
                "turn": {"id": turn_id, "status": "inProgress"},
            },
        }), flush=True)
        if model == "gpt-fake-review":
            print(json.dumps({
                "method": "item/agentMessage/delta",
                "params": {
                    "threadId": thread_id,
                    "turnId": turn_id,
                    "itemId": "fixture-draft",
                    "delta": '{"draft":true}',
                },
            }), flush=True)
            print(json.dumps({
                "method": "item/completed",
                "params": {
                    "threadId": thread_id,
                    "turnId": turn_id,
                    "item": {
                        "id": "fixture-draft",
                        "type": "agentMessage",
                        "text": '{"draft":true}',
                    },
                },
            }), flush=True)
        midpoint = len(output) // 2
        for part in (output[:midpoint], output[midpoint:]):
            print(json.dumps({
                "method": "item/agentMessage/delta",
                "params": {
                    "threadId": thread_id,
                    "turnId": turn_id,
                    "delta": part,
                },
            }), flush=True)
        print(json.dumps({
            "method": "item/completed",
            "params": {
                "threadId": thread_id,
                "turnId": turn_id,
                "item": {
                    "id": "fixture-final",
                    "type": "agentMessage",
                    "text": output,
                },
            },
        }), flush=True)
        print(json.dumps({
            "method": "thread/tokenUsage/updated",
            "params": {
                "threadId": thread_id,
                "turnId": turn_id,
                "tokenUsage": {
                    "total": {
                        "inputTokens": 120,
                        "cachedInputTokens": 0,
                        "outputTokens": 40,
                        "reasoningOutputTokens": 10,
                        "totalTokens": 160,
                    },
                    "last": {
                        "inputTokens": 120,
                        "cachedInputTokens": 0,
                        "outputTokens": 40,
                        "reasoningOutputTokens": 10,
                        "totalTokens": 160,
                    },
                    "modelContextWindow": 1000,
                },
            },
        }), flush=True)
        print(json.dumps({
            "method": "turn/completed",
            "params": {
                "threadId": thread_id,
                "turn": {"id": turn_id, "status": "completed"},
            },
        }), flush=True)
