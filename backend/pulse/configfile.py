"""Config-as-code: an optional ``pulse.yaml`` for GitOps users.

Precedence (highest first): config file > UI overrides > environment > defaults.
Example::

    track: [github, aws, cloudflare]
    exclusive: false          # true = untrack catalog services not listed here
    custom:
      - id: nas
        name: My NAS
        category: custom
        probes:
          - {kind: http, url: "http://192.168.1.10:5000"}
    settings:
      fail_threshold: 3
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from pulse.catalog.schema import ServiceDef
from pulse.runtime_settings import EDITABLE, coerce

log = logging.getLogger("pulse.config")


class ConfigFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    track: list[str] = Field(default_factory=list)
    exclusive: bool = False
    custom: list[ServiceDef] = Field(default_factory=list)
    settings: dict[str, Any] = Field(default_factory=dict)


def find_config(explicit: Path | None, data_dir: Path) -> Path | None:
    for p in (explicit, data_dir / "pulse.yaml", data_dir / "pulse.yml"):
        if p and p.is_file():
            return p
    return None


def load_config(path: Path) -> ConfigFile:
    data = yaml.safe_load(path.read_text()) or {}
    cfg = ConfigFile.model_validate(data)
    unknown = set(cfg.settings) - set(EDITABLE)
    if unknown:
        raise ValueError(f"{path}: unknown settings {sorted(unknown)}")
    for k, v in cfg.settings.items():
        cfg.settings[k] = coerce(k, v)
    return cfg


async def apply_config(rt: Any, cfg: ConfigFile) -> None:
    """Seed tracked services and custom services from the config file."""
    store = rt.store
    missing = [s for s in cfg.track if s not in rt.catalog.services]
    if missing:
        log.warning("config file tracks unknown services: %s", missing)
    for sid in cfg.track:
        if sid in rt.catalog.services:
            await store.set_tracked(sid, True)
    if cfg.exclusive:
        keep = set(cfg.track)
        for row in await store.list_services(tracked=True):
            if row["source"] == "catalog" and row["id"] not in keep:
                await store.set_tracked(row["id"], False)
    config_ids = {svc.id for svc in cfg.custom}
    for svc in cfg.custom:
        if svc.id in rt.catalog.services:
            raise ValueError(f"custom service id '{svc.id}' clashes with a catalog service")
        await store.save_custom_service(
            svc.id, svc.name, svc.category, svc.model_dump(mode="json"), "config"
        )
    # Services that were defined in an earlier version of the file are removed.
    for row in await store.list_services():
        if row["source"] == "config" and row["id"] not in config_ids:
            await store.delete_service(row["id"])
