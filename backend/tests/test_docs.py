"""Keep docs honest: every setting is documented, every documented one exists."""

from __future__ import annotations

import re
from pathlib import Path

from pulse.config import Settings

DOC = Path(__file__).resolve().parents[2] / "docs" / "CONFIGURATION.md"
INTERNAL = {"version"}


def test_every_setting_is_documented() -> None:
    text = DOC.read_text()
    missing = [
        f"PULSE_{n.upper()}"
        for n in Settings.model_fields
        if n not in INTERNAL and f"PULSE_{n.upper()}" not in text
    ]
    assert not missing, f"undocumented settings: {missing}"


def test_no_documented_setting_is_stale() -> None:
    documented = set(re.findall(r"`PULSE_([A-Z0-9_]+)`", DOC.read_text()))
    known = {n.upper() for n in Settings.model_fields}
    stale = sorted(d for d in documented if d not in known and not d.endswith("_FILE"))
    assert not stale, f"documented but not a setting: {stale}"
