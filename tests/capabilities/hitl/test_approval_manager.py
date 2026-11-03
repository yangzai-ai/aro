"""CLI approval must be explicit, reject cancellation and clear pending state."""

import pytest

from capabilities.hitl.approval_manager import ApprovalManager


@pytest.mark.parametrize(
    "answer,expected", [("y", True), (" YES ", True), ("n", False), ("no", False)]
)
async def test_explicit_decisions(monkeypatch, capsys, answer, expected):
    manager = ApprovalManager()

    def respond(prompt):
        request = next(iter(manager.pending.values()))
        assert request.thread_id == "session"
        assert request.tool_args == {"id": "TKT-001"}
        return answer

    monkeypatch.setattr("builtins.input", respond)
    assert await manager.request_approval("approve", {"id": "TKT-001"}, "session") is expected
    assert manager.pending == {}
    output = capsys.readouterr().out
    assert "HITL Approval Required" in output and "TKT-001" in output


async def test_invalid_answer_reprompts(monkeypatch):
    answers = iter(["", "maybe", "yes"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    assert await ApprovalManager().request_approval("write", {}, "s")


@pytest.mark.parametrize("exception", [EOFError, KeyboardInterrupt, RuntimeError])
async def test_input_failure_never_approves(monkeypatch, exception):
    manager = ApprovalManager()

    def fail(_):
        raise exception("cancelled")

    monkeypatch.setattr("builtins.input", fail)
    if exception is RuntimeError:
        with pytest.raises(RuntimeError):
            await manager.request_approval("write", {}, "s")
    else:
        assert not await manager.request_approval("write", {}, "s")
    assert manager.pending == {}


async def test_unsupported_modes():
    with pytest.raises(ValueError):
        ApprovalManager("invalid")
    assert ApprovalManager("api").mode == "api"
    with pytest.raises(ValueError):
        ApprovalManager(timeout_seconds=0)
