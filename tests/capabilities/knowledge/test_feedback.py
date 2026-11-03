"""Append-only feedback validation and Unicode persistence."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from capabilities.knowledge.feedback import FeedbackCollector, SearchFeedback


def test_feedback(tmp_path: Path) -> None:
    """Two events append without overwriting; invalid ratings and clicks fail."""
    file = tmp_path / "nested/feedback.jsonl"
    collector = FeedbackCollector(file)
    event = SearchFeedback(query="规范", results=["a"], user_clicked=0, user_rating=5)
    collector.collect(event)
    collector.collect(event)
    lines = file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and json.loads(lines[0])["query"] == "规范"
    with pytest.raises(ValidationError):
        SearchFeedback(query="q", results=[], user_clicked=0)
    with pytest.raises(ValidationError):
        SearchFeedback(query="q", results=[], user_rating=6)
