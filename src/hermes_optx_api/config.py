"""Configuration for hermes-optx-api."""

import os
from pathlib import Path
from pydantic import BaseModel


class Settings(BaseModel):
    """Application settings, loaded from environment variables."""

    hermes_agent_url: str = os.getenv("HERMES_AGENT_URL", "http://localhost:8642")
    hermes_home: Path = Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes")))
    host: str = os.getenv("OPTX_API_HOST", "0.0.0.0")
    port: int = int(os.getenv("OPTX_API_PORT", "8643"))
    api_key: str = os.getenv("API_KEY", "")
    memory_backend: str = os.getenv("MEMORY_BACKEND", "holographic")
    memory_db_url: str = os.getenv("MEMORY_DB_URL", "")
    spacetimedb_db: str = os.getenv("SPACETIMEDB_DB", "")
    debug: bool = os.getenv("DEBUG", "").lower() in ("1", "true", "yes")

    # MPP (Machine Payments Protocol) settings
    mpp_enabled: bool = os.getenv("MPP_ENABLED", "").lower() in ("1", "true", "yes")
    mpp_recipient: str = os.getenv("MPP_RECIPIENT", "")  # Tempo wallet address (0x...)
    mpp_amount_per_request: str = os.getenv("MPP_AMOUNT", "0.10")  # pathUSD per request
    mpp_network: str = os.getenv("MPP_NETWORK", "testnet")  # testnet | mainnet
    mpp_fee_payer_key: str = os.getenv("MPP_FEE_PAYER_KEY", "")  # optional: sponsor gas

    @property
    def config_path(self) -> Path:
        return self.hermes_home / "config.yaml"

    @property
    def state_db_path(self) -> Path:
        return self.hermes_home / "state.db"

    @property
    def skills_dir(self) -> Path:
        return self.hermes_home / "skills"

    @property
    def memories_dir(self) -> Path:
        return self.hermes_home / "memories"

    @property
    def env_path(self) -> Path:
        return self.hermes_home / ".env"


settings = Settings()
