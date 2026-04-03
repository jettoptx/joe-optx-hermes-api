"""Config API — read/write Hermes Agent configuration."""

from typing import Any, Optional

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from hermes_optx_api.config import settings

router = APIRouter()


def _load_config() -> dict:
    """Load Hermes config.yaml."""
    config_path = settings.config_path
    if not config_path.exists():
        return {}
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _save_config(config: dict):
    """Write Hermes config.yaml."""
    config_path = settings.config_path
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)


def _load_env() -> dict[str, str]:
    """Load ~/.hermes/.env as key-value pairs."""
    env_path = settings.env_path
    if not env_path.exists():
        return {}
    env_vars = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            env_vars[key.strip()] = value.strip()
    return env_vars


def _redact(key: str, value: str) -> str:
    """Redact sensitive values for display."""
    sensitive_keys = {"api_key", "password", "secret", "token"}
    if any(s in key.lower() for s in sensitive_keys):
        if len(value) > 8:
            return value[:4] + "..." + value[-4:]
        return "***"
    return value


@router.get("/config")
async def get_config():
    """Get the current Hermes Agent configuration (redacted)."""
    config = _load_config()
    env = _load_env()

    # Redact sensitive values
    safe_env = {k: _redact(k, v) for k, v in env.items()}

    return {
        "config": config,
        "env": safe_env,
        "config_path": str(settings.config_path),
        "hermes_home": str(settings.hermes_home),
    }


class ConfigUpdate(BaseModel):
    key: str
    value: Any


@router.post("/config")
async def update_config(update: ConfigUpdate):
    """Update a config value (dot-notation key, e.g. 'model.provider')."""
    config = _load_config()

    # Navigate dot-notation path
    keys = update.key.split(".")
    target = config
    for k in keys[:-1]:
        if k not in target or not isinstance(target[k], dict):
            target[k] = {}
        target = target[k]

    target[keys[-1]] = update.value
    _save_config(config)

    return {"updated": True, "key": update.key, "value": update.value}


@router.get("/config/model")
async def get_model_config():
    """Get the current model/provider configuration."""
    config = _load_config()
    env = _load_env()

    model_config = config.get("model", {})
    llm_config = config.get("llm", {})

    return {
        "provider": model_config.get("provider", llm_config.get("provider", "auto")),
        "model": model_config.get("name", llm_config.get("model", "")),
        "base_url": model_config.get("base_url", llm_config.get("base_url", "")),
        "temperature": llm_config.get("temperature", 0.7),
        "max_tokens": llm_config.get("max_tokens", 4096),
    }


@router.post("/config/model")
async def update_model_config(
    provider: Optional[str] = None,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
):
    """Update model/provider configuration."""
    config = _load_config()

    if "model" not in config:
        config["model"] = {}

    if provider is not None:
        config["model"]["provider"] = provider
    if model is not None:
        config["model"]["name"] = model
    if base_url is not None:
        config["model"]["base_url"] = base_url

    _save_config(config)
    return {"updated": True, "model": config["model"]}
