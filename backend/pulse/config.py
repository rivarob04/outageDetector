"""Runtime settings, loaded from ``PULSE_*`` environment variables.

Every secret can also be supplied as ``PULSE_<NAME>_FILE`` pointing at a file
(Docker secrets); the file content wins over the plain variable.
"""

from __future__ import annotations

import os
import secrets
from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from pulse import __version__

_SECRET_FIELDS = ("secret_key", "admin_password", "mqtt_password", "metrics_token", "agent_token")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PULSE_", extra="ignore")

    # Core
    data_dir: Path = Path("/data")
    database_url: str = ""  # default: sqlite in data_dir
    host: str = "0.0.0.0"  # noqa: S104 - container default
    port: int = 8080
    log_level: str = "INFO"
    log_json: bool = True
    base_url: str = ""  # public URL, used in notifications/feeds
    secret_key: str = ""
    demo: bool = False
    static_dir: Path | None = None
    catalog_dir: Path | None = None
    config_file: Path | None = None  # config-as-code (pulse.yaml)

    # Auth
    public_view: bool = True
    admin_username: str = ""
    admin_password: str = ""
    trusted_auth_header: str = ""  # e.g. Remote-User
    # Only these peers may set the trusted auth header. Keep it to your reverse proxy:
    # LAN ranges are deliberately not included (anyone on the LAN could forge the header).
    trusted_proxies: str = "127.0.0.1/32,::1/128,172.16.0.0/12"
    cookie_secure: bool = False

    # Polling
    feed_interval_s: int = 60
    probe_interval_s: int = 60
    canary_interval_s: int = 30
    max_concurrency: int = 20
    http_timeout_s: float = 10.0
    user_agent: str = ""
    canary_targets: str = (
        "https://1.1.1.1,https://dns.google,https://www.msftconnecttest.com/connecttest.txt"
    )
    dns_resolvers: str = "1.1.1.1,8.8.8.8,9.9.9.9"
    allow_private_targets: bool = False

    # Verdict
    fail_threshold: int = 2
    recover_threshold: int = 3
    latency_factor: float = 3.0
    latency_min_ms: float = 300.0

    # Retention
    retention_raw_days: int = 7
    retention_5m_days: int = 30
    retention_incident_days: int = 365

    # Agents
    agent_push_interval_s: int = 60

    # Integrations
    metrics_enabled: bool = True
    metrics_token: str = ""
    mqtt_url: str = ""  # mqtt://host:1883
    mqtt_username: str = ""
    mqtt_password: str = ""
    mqtt_discovery_prefix: str = "homeassistant"
    mqtt_base_topic: str = "pulse"
    map_style_url: str = ""
    host_location: str = ""  # "lat,lon" of this hub for the map (optional)
    # Canadian postal code (e.g. "N8W 1H4") for local ISP outage lookups (Cogeco, TekSavvy, Bell).
    # Optional: without it those feeds fall back to province-wide / all-area outages.
    postal_code: str = ""
    # Agent mode (`pulse agent`)
    hub_url: str = ""
    agent_token: str = ""
    agent_location: str = ""
    public_ip_lookup_url: str = ""  # off by default (privacy)

    # Advanced signals
    feature_chatter: bool = False
    feature_anomaly: bool = True
    feature_correlation: bool = True

    version: str = Field(default=__version__)

    @model_validator(mode="before")
    @classmethod
    def _read_secret_files(cls, data: dict[str, object]) -> dict[str, object]:
        for name in _SECRET_FIELDS:
            path = os.environ.get(f"PULSE_{name.upper()}_FILE")
            if path and Path(path).is_file():
                data[name] = Path(path).read_text().strip()
        return data

    @property
    def db_url(self) -> str:
        if self.database_url:
            url = self.database_url
            if url.startswith("postgres://"):
                url = "postgresql+asyncpg://" + url[len("postgres://") :]
            elif url.startswith("postgresql://"):
                url = "postgresql+asyncpg://" + url[len("postgresql://") :]
            return url
        return f"sqlite+aiosqlite:///{self.data_dir / 'pulse.db'}"

    @property
    def is_sqlite(self) -> bool:
        return self.db_url.startswith("sqlite")

    @property
    def ua(self) -> str:
        return self.user_agent or (
            f"Pulse/{self.version} (+https://github.com/rivarob04/outageDetector)"
        )

    def resolved_static_dir(self) -> Path | None:
        candidates = [self.static_dir] if self.static_dir else []
        here = Path(__file__).resolve()
        candidates += [here.parents[2] / "frontend" / "dist", Path("/app/static")]
        for c in candidates:
            if c and (c / "index.html").is_file():
                return c
        return None

    def resolved_catalog_dir(self) -> Path:
        if self.catalog_dir:
            return self.catalog_dir
        here = Path(__file__).resolve()
        for c in (here.parents[2] / "catalog", Path("/app/catalog")):
            if c.is_dir():
                return c
        return here.parents[2] / "catalog"

    def ensure_secret_key(self) -> str:
        """Use the configured key, else a persisted random key in data_dir."""
        if self.secret_key:
            return self.secret_key
        key_file = self.data_dir / ".secret_key"
        if key_file.is_file():
            self.secret_key = key_file.read_text().strip()
        else:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self.secret_key = secrets.token_hex(32)
            key_file.write_text(self.secret_key)
            key_file.chmod(0o600)
        return self.secret_key

    def list_setting(self, value: str) -> list[str]:
        return [v.strip() for v in value.split(",") if v.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
