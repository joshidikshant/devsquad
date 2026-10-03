#!/usr/bin/env python3
import json, sys, time
initialized = False
for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    if method == "initialize":
        print(json.dumps({"id":request["id"],"result":{"serverInfo":{"name":"fake","version":"1"}}}), flush=True)
    elif method == "initialized":
        initialized = True
    elif method == "model/list":
        if not initialized:
            print(json.dumps({"id":request["id"],"error":{"code":-32002,"message":"not initialized"}}), flush=True); continue
        cursor = request.get("params",{}).get("cursor")
        result = {"data":[{"id":"gpt-fake","supportedReasoningEfforts":[{"reasoningEffort":"low","description":"fixture"}]}],"nextCursor":"two"} if cursor is None else {"data":[],"nextCursor":None}
        print(json.dumps({"method":"account/updated","params":{"reason":"fixture"}}), flush=True)
        print(json.dumps({"id":request["id"],"result":result}), flush=True)
    elif method == "turn/start":
        if not initialized:
            print(json.dumps({"id":request["id"],"error":{"code":-32002,"message":"not initialized"}}), flush=True); continue
        print(json.dumps({"id":request["id"],"result":{"turn":{"id":"turn-1"}}}), flush=True)
        time.sleep(0.02)
        print(json.dumps({"method":"turn/started","params":{"threadId":request["params"]["threadId"],"turn":{"id":"turn-1","status":"inProgress"}}}), flush=True)
        time.sleep(0.02)
        print(json.dumps({"method":"item/agentMessage/delta","params":{"threadId":request["params"]["threadId"],"turnId":"turn-1","delta":"ok"}}), flush=True)
        time.sleep(0.02)
        print(json.dumps({"method":"turn/completed","params":{"threadId":request["params"]["threadId"],"turn":{"id":"turn-1","status":"completed"}}}), flush=True)
