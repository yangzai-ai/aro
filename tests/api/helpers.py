"""A real ASGI receive/send harness that does not buffer the SSE response."""

import asyncio
import json
from contextlib import suppress
from typing import Any

from fastapi import FastAPI
from langchain_core.messages import ToolMessage

from tests.test_kernel_loop import FakeLLMProvider


class StreamingProvider(FakeLLMProvider):
    """Emit interleaved network-sized text/argument chunks without HTTP calls."""

    def __init__(self):
        super().__init__("")

    async def chat_stream(self, messages, tools=None, **kwargs):
        if isinstance(messages[-1], ToolMessage):
            text = (
                "操作未执行（模拟数据）。"
                if "Error:" in str(messages[-1].content)
                else "处理完成（模拟数据）。"
            )
            for char in text:
                yield {"delta": char}
                await asyncio.sleep(0)
            yield {"finish_reason": "stop"}
            return
        question = str(messages[-1].content)
        if question == "failure":
            raise RuntimeError("BACKEND-SECRET-SENTINEL postgres://user:password@host/db")
        if question == "secret":
            yield {"delta": "BACKEND-SECRET-SENTINEL postgres://user:password@host/db"}
            yield {"finish_reason": "stop"}
            return
        name = "update_ticket" if question == "approve" else "query_tickets"
        args = (
            '{"ticket_id":"TKT-001","status":"resolved"}'
            if question == "approve"
            else '{"status":"open"}'
        )
        yield {
            "tool_calls_delta": [
                {"index": 0, "id": "c1", "function": {"name": name[:5], "arguments": args[:9]}}
            ]
        }
        await asyncio.sleep(0)
        yield {
            "tool_calls_delta": [
                {"index": 0, "function": {"name": name[5:], "arguments": args[9:]}}
            ]
        }
        yield {"finish_reason": "tool_calls"}


class OpenChat:
    """Drive POST /chat and inspect each SSE event before the response completes."""

    def __init__(self, app: FastAPI, message: str, session_id: str | None = None, token: str = ""):
        self.app = app
        self.payload = json.dumps({"message": message, "session_id": session_id}).encode()
        self.headers = [(b"content-type", b"application/json")]
        if token:
            self.headers.append((b"authorization", f"Bearer {token}".encode()))
        self.events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.disconnect = asyncio.Event()
        self.task = None
        self.body_sent = False
        self.raw = bytearray()

    async def __aenter__(self):
        async def receive():
            if not self.body_sent:
                self.body_sent = True
                return {"type": "http.request", "body": self.payload, "more_body": False}
            await self.disconnect.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.start":
                self.status = message["status"]
                self.response_headers = dict(message["headers"])
            elif message["type"] == "http.response.body":
                body = message.get("body", b"")
                self.raw.extend(body)
                for frame in body.decode().split("\n\n"):
                    if frame.startswith("event:"):
                        lines = frame.splitlines()
                        await self.events.put(
                            {"type": lines[0][7:], "data": json.loads(lines[1][6:])}
                        )

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/chat",
            "raw_path": b"/chat",
            "query_string": b"",
            "root_path": "",
            "headers": self.headers,
            "client": ("127.0.0.1", 1234),
            "server": ("test", 80),
        }
        self.task = asyncio.create_task(self.app(scope, receive, send))
        return self

    async def next(self, expected: str | None = None):
        event = await asyncio.wait_for(self.events.get(), timeout=3)
        if expected is not None:
            assert event["type"] == expected, event
        return event

    async def until(self, kind: str):
        events = []
        while True:
            event = await self.next()
            events.append(event)
            if event["type"] == kind:
                return events

    async def __aexit__(self, *args):
        self.disconnect.set()
        try:
            await asyncio.wait_for(self.task, 2)
        except TimeoutError:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
