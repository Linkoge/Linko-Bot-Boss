import asyncio
import os
from types import SimpleNamespace

from ai_manager.codex import AppServer


class FakeServer(AppServer):
    def __init__(self, settings):
        super().__init__(settings)
        self.process = SimpleNamespace(pid=os.getpid())
        self.account_type = "chatgpt"
        self.prompts = []
        self.catalog = [
            {
                "model": "test-model",
                "displayName": "Test model",
                "isDefault": True,
                "supportedReasoningEfforts": [{"reasoningEffort": e} for e in ("low", "medium", "high", "ultra")],
                "defaultReasoningEffort": "low",
            }
        ]

    async def start(self):
        pass

    async def thread(self, cwd, instructions, thread_id=None, model=None):
        return thread_id or f"thread-{len(self.prompts)}"

    async def request(self, method, params, timeout=30):
        if method != "turn/start":
            return {}
        self.prompts.append(params["input"][0]["text"])
        thread_id = params["threadId"]
        queue = self.queues[thread_id]

        async def emit():
            await asyncio.sleep(0.01)
            queue.put_nowait(
                {
                    "method": "item/agentMessage/delta",
                    "params": {"threadId": thread_id, "turnId": "turn-test", "delta": "Saved streamed answer"},
                }
            )
            queue.put_nowait(
                {
                    "method": "turn/completed",
                    "params": {"threadId": thread_id, "turn": {"id": "turn-test", "status": "completed"}},
                }
            )

        asyncio.create_task(emit())
        return {"turn": {"id": "turn-test"}}

    async def close(self):
        pass
