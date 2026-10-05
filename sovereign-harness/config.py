"""Configuration management for Sovereign Harness (multi-provider)."""
import os
import json
from pathlib import Path

CONFIG_DIR   = Path.home() / ".sovereign"
CONFIG_FILE  = CONFIG_DIR  / "config.json"
HISTORY_FILE = CONFIG_DIR  / "input_history.txt"
SESSIONS_DIR = CONFIG_DIR  / "sessions"


def _resolve_default_model() -> str:
    """Resolve default model with this priority:
    1. SOVEREIGN_MODEL environment variable
    2. Parse FROM line from Modelfile (SOVEREIGN_MODELFILE env, Modelfile in cwd or ~/.sovereign/Modelfile)
    3. Fallback to qwen2.5-coder-7b-instruct
    """
    # Highest priority: explicit env var
    env_model = os.environ.get("SOVEREIGN_MODEL", "").strip()
    if env_model:
        return env_model

    # Try Modelfile candidates (hardened: no /home/mike or projects_clean; use ~/.sovereign/ and relative)
    candidates = []
    modelfile_env = os.environ.get("SOVEREIGN_MODELFILE", "").strip()
    if modelfile_env:
        candidates.append(Path(modelfile_env).expanduser())
    # local in current working dir
    candidates.append(Path.cwd() / "Modelfile")
    # consistent with CONFIG_DIR ~/.sovereign/
    candidates.append(CONFIG_DIR / "Modelfile")

    for mf in candidates:
        if mf.exists() and mf.is_file():
            try:
                for line in mf.read_text().splitlines():
                    line = line.strip()
                    if line.upper().startswith("FROM "):
                        raw = line[5:].strip()
                        # handle model:tag or full path/to/model.gguf
                        if ":" in raw and not raw.lower().endswith(".gguf"):
                            raw = raw.split(":")[0]
                        model = Path(raw).stem  # drop .gguf and path
                        # strip common quantization suffixes
                        for suf in ("-q4_k_m", "-q5_k_m", "-q8_0", "-q4_0", "-f16", "-q3_k_m", "-q2_k"):
                            if model.endswith(suf):
                                model = model[: -len(suf)]
                                break
                        if model:
                            return model
            except Exception:
                pass

    return "qwen2.5-coder-7b-instruct"

DEFAULTS = {
    "model":            "",  # set via SOVEREIGN_MODEL env, Modelfile, or --model
    "max_tokens":       8192,
    "permission_mode":  "auto",   # auto | accept-all | manual
    "verbose":          False,
    "thinking":         False,
    "thinking_budget":  10000,
    "custom_base_url":  "",       # for "custom" provider
    "max_tool_output":  32000,
    "max_agent_depth":  3,
    "max_concurrent_agents": 3,
    # Per-provider API keys (optional; env vars take priority)
    # "anthropic_api_key": "sk-ant-..."
    # "openai_api_key":    "sk-..."
    # "gemini_api_key":    "..."
    # "kimi_api_key":      "..."
    # "qwen_api_key":      "..."
    # "zhipu_api_key":     "..."
    # "deepseek_api_key":  "..."
}


def load_config() -> dict:
    CONFIG_DIR.mkdir(exist_ok=True)
    SESSIONS_DIR.mkdir(exist_ok=True)
    cfg = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        try:
            cfg.update(json.loads(CONFIG_FILE.read_text()))
        except Exception:
            pass

    # Resolve model if not explicitly set in config file
    if not cfg.get("model"):
        cfg["model"] = _resolve_default_model()

    # Backward-compat: legacy single api_key → anthropic_api_key
    if cfg.get("api_key") and not cfg.get("anthropic_api_key"):
        cfg["anthropic_api_key"] = cfg.pop("api_key")
    # Also accept ANTHROPIC_API_KEY env for backward-compat
    if not cfg.get("anthropic_api_key"):
        cfg["anthropic_api_key"] = os.environ.get("ANTHROPIC_API_KEY", "")
    return cfg


def save_config(cfg: dict):
    CONFIG_DIR.mkdir(exist_ok=True)
    data = dict(cfg)
    CONFIG_FILE.write_text(json.dumps(data, indent=2))


def current_provider(cfg: dict) -> str:
    from providers import detect_provider
    return detect_provider(cfg.get("model") or "qwen2.5-coder-7b-instruct")


def has_api_key(cfg: dict) -> bool:
    """Check whether the active provider has an API key configured."""
    from providers import get_api_key
    pname = current_provider(cfg)
    key = get_api_key(pname, cfg)
    return bool(key)


def calc_cost(model: str, in_tokens: int, out_tokens: int) -> float:
    from providers import calc_cost as _cc
    return _cc(model, in_tokens, out_tokens)
