"""Model resolution chooses complete cached weights without network access."""

from pathlib import Path
from typing import Any
from unittest.mock import Mock

from capabilities.knowledge.model_files import MODEL_FILES, resolve_model_path


def test_local_path(tmp_path: Path) -> None:
    """Explicit deployment paths bypass the hub entirely."""
    assert resolve_model_path(str(tmp_path)) == str(tmp_path)


def test_cached_and_downloaded_paths(tmp_path: Path, monkeypatch: Any) -> None:
    """Incomplete cache triggers a bounded-file download; a complete cache does not."""
    resolver = Mock(return_value=str(tmp_path))
    monkeypatch.setattr("huggingface_hub.snapshot_download", resolver)
    assert resolve_model_path("BAAI/bge-m3") == str(tmp_path)
    resolver.assert_any_call("BAAI/bge-m3", allow_patterns=MODEL_FILES)
    for filename in ["config.json", "tokenizer_config.json", "pytorch_model.bin"]:
        (tmp_path / filename).write_text("test", encoding="utf-8")
    resolver.reset_mock()
    assert resolve_model_path("BAAI/bge-m3") == str(tmp_path)
    resolver.assert_called_once_with("BAAI/bge-m3", local_files_only=True)
    resolver.reset_mock()
    resolver.side_effect = [FileNotFoundError(), str(tmp_path)]
    assert resolve_model_path("BAAI/bge-m3") == str(tmp_path)
