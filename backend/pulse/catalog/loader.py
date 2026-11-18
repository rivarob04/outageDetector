"""Load and validate the provider catalog."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from pulse.catalog.schema import Region, ServiceDef, VerificationEntry

log = logging.getLogger("pulse.catalog")

REGIONS_FILE = "regions.yaml"
VERIFICATION_FILE = "verification.json"


@dataclass
class Catalog:
    services: dict[str, ServiceDef] = field(default_factory=dict)
    regions: dict[str, Region] = field(default_factory=dict)
    verification: dict[str, VerificationEntry] = field(default_factory=dict)

    def is_verified(self, service_id: str) -> bool:
        v = self.verification.get(service_id)
        return bool(v and v.verified)

    def get(self, service_id: str) -> ServiceDef | None:
        return self.services.get(service_id)


class CatalogError(ValueError):
    pass


def load_catalog(directory: Path) -> Catalog:
    cat = Catalog()
    if not directory.is_dir():
        log.warning("catalog directory %s missing", directory)
        return cat
    errors: list[str] = []
    for path in sorted(directory.glob("*.yaml")):
        if path.name == REGIONS_FILE:
            continue
        try:
            data = yaml.safe_load(path.read_text())
            svc = ServiceDef.model_validate(data)
        except Exception as exc:
            errors.append(f"{path.name}: {exc}")
            continue
        if svc.id != path.stem:
            errors.append(f"{path.name}: id '{svc.id}' must match file name")
            continue
        cat.services[svc.id] = svc
    regions_path = directory / REGIONS_FILE
    if regions_path.is_file():
        for item in yaml.safe_load(regions_path.read_text()) or []:
            region = Region.model_validate(item)
            cat.regions[region.id] = region
    ver_path = directory / VERIFICATION_FILE
    if ver_path.is_file():
        for item in json.loads(ver_path.read_text()).get("results", []):
            entry = VerificationEntry.model_validate(item)
            cat.verification[entry.service_id] = entry
    for svc in cat.services.values():
        for dep in svc.depends_on:
            base = dep.split(":", 1)[0]
            if base not in cat.services:
                errors.append(f"{svc.id}: depends_on unknown service '{base}'")
            if ":" in dep and dep not in cat.regions:
                errors.append(f"{svc.id}: depends_on unknown region '{dep}'")
    if errors:
        raise CatalogError("invalid catalog:\n  " + "\n  ".join(errors))
    return cat


@lru_cache(maxsize=4)
def cached_catalog(directory: str) -> Catalog:
    return load_catalog(Path(directory))
