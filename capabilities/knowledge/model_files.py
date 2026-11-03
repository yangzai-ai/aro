"""Resolve minimal local model snapshots without downloading unused ONNX assets."""

from pathlib import Path

MODEL_FILES = [
    "*.json",
    "*.pt",
    "model.safetensors",
    "pytorch_model.bin",
    "sentencepiece.bpe.model",
]


def resolve_model_path(model_name: str) -> str:
    """Prefer complete local weights; download only inference files if not cached.

    Normal inference with cached weights makes no network request. Users may
    configure an explicit local model directory for fully offline deployments.
    """
    if Path(model_name).is_dir():
        return model_name
    from huggingface_hub import snapshot_download

    try:
        cached = Path(snapshot_download(model_name, local_files_only=True))
    except FileNotFoundError:
        cached = None
    if (
        cached is not None
        and ((cached / "model.safetensors").is_file() or (cached / "pytorch_model.bin").is_file())
        and (cached / "config.json").is_file()
        and (cached / "tokenizer_config.json").is_file()
    ):
        return str(cached)
    return str(snapshot_download(model_name, allow_patterns=MODEL_FILES))
