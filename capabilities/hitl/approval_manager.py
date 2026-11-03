"""Approval requests for CLI input and asynchronous HTTP callbacks."""

import asyncio
import json
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from core.kernel.events import emit_event


class ApprovalTimeoutError(TimeoutError):
    """No decision arrived before the approval expired."""


@dataclass
class ApprovalRequest:
    """A pending approval request."""

    tool_name: str
    tool_args: dict[str, Any]
    thread_id: str
    timestamp: float
    expires_at: float | None = None


class ApprovalManager:
    """CLI/API approval manager with bounded, cancellable pending requests."""

    def __init__(self, mode: str = "cli", timeout_seconds: float = 300) -> None:
        if mode not in {"cli", "api"}:
            raise ValueError("HITL mode must be 'cli' or 'api'")
        self.mode = mode
        if timeout_seconds <= 0:
            raise ValueError("Approval timeout must be positive")
        self.timeout_seconds = timeout_seconds
        self.pending: dict[str, ApprovalRequest] = {}
        self._decisions: dict[str, asyncio.Future[bool]] = {}

    async def request_approval(
        self, tool_name: str, tool_args: dict[str, Any], thread_id: str
    ) -> bool:
        """Request approval, blocking for input in CLI mode."""
        request_id = str(uuid.uuid4())
        now = time.time()
        self.pending[request_id] = ApprovalRequest(
            tool_name,
            deepcopy(tool_args),
            thread_id,
            now,
            now + self.timeout_seconds if self.mode == "api" else None,
        )
        try:
            if self.mode == "api":
                decision: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
                self._decisions[request_id] = decision
                await emit_event(
                    "approval_required",
                    approval_id=request_id,
                    tool_name=tool_name,
                    args=deepcopy(tool_args),
                    thread_id=thread_id,
                    expires_at=now + self.timeout_seconds,
                )
                try:
                    return await asyncio.wait_for(decision, self.timeout_seconds)
                except TimeoutError as exc:
                    await emit_event(
                        "error",
                        code="approval_timeout",
                        approval_id=request_id,
                        message="Approval timed out; operation rejected",
                        terminal=False,
                    )
                    raise ApprovalTimeoutError("Approval timed out; operation rejected") from exc
            return await asyncio.to_thread(self._read_cli_answer, tool_name, tool_args)
        finally:
            self.pending.pop(request_id, None)
            future = self._decisions.pop(request_id, None)
            if future is not None and not future.done():
                future.cancel()

    def decide(self, approval_id: str, thread_id: str, approved: bool) -> None:
        """Resolve exactly one unexpired request belonging to this session."""
        request = self.pending.get(approval_id)
        future = self._decisions.get(approval_id)
        if request is None or future is None or request.thread_id != thread_id:
            raise KeyError("Approval not found")
        if future.done() or (request.expires_at is not None and time.time() >= request.expires_at):
            raise ValueError("Approval already decided or expired")
        future.set_result(approved)

    @staticmethod
    def _read_cli_answer(tool_name: str, tool_args: dict[str, Any]) -> bool:
        """Read and validate a y/n answer without blocking the event loop."""
        print("\n" + "=" * 60)
        print("HITL Approval Required")
        print(f"Tool: {tool_name}")
        print(f"Args: {json.dumps(tool_args, indent=2, ensure_ascii=False)}")
        print("=" * 60)
        while True:
            try:
                answer = input("Approve? (y/n): ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("Approval cancelled; operation rejected")
                return False
            if answer in {"y", "yes"}:
                print("Approved")
                return True
            if answer in {"n", "no"}:
                print("Rejected")
                return False
            print("Please enter 'y' or 'n'")
