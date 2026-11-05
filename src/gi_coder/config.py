"""Installation-relative configuration and caller-relative explicit overrides."""
import json
import os
from pathlib import Path


ROOT = Path(os.environ.get("GI_CODER_HOME", Path(__file__).resolve().parents[2])).resolve()
MODELS = {key: {
    "repo": f"Qwen/Qwen2.5-Coder-{key.upper()}-Instruct-GGUF",
    "filename": f"qwen2.5-coder-{key}-instruct-q4_k_m.gguf",
} for key in ("7b", "3b")}


class CoderError(Exception):
    """An actionable user-facing failure."""


def load_config(root=ROOT):
    try:
        config = json.loads((root / "config/settings.json").read_text(encoding="utf-8"))
        local = root / "config/local.json"
        if local.exists():
            config.update(json.loads(local.read_text(encoding="utf-8")))
        config.setdefault('assessment_max_tokens', 384)
        config.setdefault('backend', 'auto')
        config.setdefault('gpu_layers', 99)
        config.setdefault('debug_assessment', False)
        if type(config['debug_assessment']) is not bool:
            raise ValueError('debug_assessment must be true or false')
        if config["model"] not in MODELS:
            raise ValueError("model must be 7b or 3b")
        for name, low, high in (("context_size", 512, 32768), ("threads", 1, 256),
                                ("max_tokens", 16, 32768), ('assessment_max_tokens', 128, 4096),
                                ('gpu_layers', 0, 999), ("load_timeout", 1, 86400),
                                ("generation_timeout", 1, 86400)):
            if type(config[name]) is not int or not low <= config[name] <= high:
                raise ValueError(f"{name} must be an integer from {low} to {high}")
        if config["max_tokens"] >= config["context_size"]:
            raise ValueError("max_tokens must be smaller than context_size")
        if config['assessment_max_tokens'] >= config['context_size']:
            raise ValueError('assessment_max_tokens must be smaller than context_size')
        if config['backend'] not in ('auto', 'metal', 'cpu'):
            raise ValueError('backend must be auto, metal or cpu')
        if not isinstance(config["temperature"], (int, float)) or not 0 <= config["temperature"] <= 2:
            raise ValueError("temperature must be between 0 and 2")
        for name in ("engine_path", "model_path"):
            if config[name] is not None and not isinstance(config[name], str):
                raise ValueError(f"{name} must be a path string or null")
        return config
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CoderError(f"Invalid configuration: {exc}") from exc


def installation_path(value, root=ROOT):
    path = Path(value).expanduser()
    return path if path.is_absolute() else root / path


def switch_model(model, root=ROOT):
    if model not in MODELS:
        raise CoderError("Choose model 7b or 3b")
    local = root / "config/local.json"
    data = json.loads(local.read_text(encoding="utf-8")) if local.exists() else {}
    data.update(model=model, model_path=None)
    temp = local.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    temp.replace(local)
