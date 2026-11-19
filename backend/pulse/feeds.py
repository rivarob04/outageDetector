"""Per-installation feed resolution (e.g. local ISP outage lookups by postal code).

Catalog feeds are static, but ISP outage trackers list outages by postal code. When the user
has set ``PULSE_POSTAL_CODE`` (one or more codes), the effective feed carries the normalized
codes in ``options.postal_codes`` (comma-separated) so the (pure) adapter can keep only
outages in those areas.
"""

from __future__ import annotations

import re

from pulse.catalog.schema import FeedConfig

# Canada Post: letter-digit-letter digit-letter-digit; D, F, I, O, Q, U never appear,
# and W, Z never start a code. The first half alone is the forward sortation area (FSA).
_FSA = r"[ABCEGHJ-NPRSTVXY]\d[ABCEGHJ-NPRSTV-Z]"
_LDU = r"\d[ABCEGHJ-NPRSTV-Z]\d"
_FULL = re.compile(rf"^{_FSA}{_LDU}$")
_FSA_ONLY = re.compile(rf"^{_FSA}$")
_LDU_ONLY = re.compile(rf"^{_LDU}$")
ERROR = (
    "postal_code must be Canadian postal codes like N8W 1H4 (or areas like N8W), "
    "separated by commas or spaces"
)


def parse_postal_codes(value: str) -> list[str]:
    """``"n8w 1h4, N8N2L9 n9j"`` -> ``["N8W1H4", "N8N2L9", "N9J"]``. Raises on anything invalid.

    Commas, semicolons and whitespace all separate codes; the space inside a single code
    ("N8W 1H4") is recognised and kept together. A three-character area (FSA) is accepted too.
    """
    tokens = [t for t in re.split(r"[\s,;]+", (value or "").upper().replace("-", "")) if t]
    codes: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if _FSA_ONLY.match(tok) and i + 1 < len(tokens) and _LDU_ONLY.match(tokens[i + 1]):
            tok += tokens[i + 1]
            i += 1
        if not (_FULL.match(tok) or _FSA_ONLY.match(tok)):
            raise ValueError(ERROR)
        if tok not in codes:
            codes.append(tok)
        i += 1
    return codes


def normalize_postal_code(value: str) -> str:
    """Canonical form of the setting: ``"N8W1H4, N8N2L9"``; ``""`` stays empty."""
    return ", ".join(parse_postal_codes(value))


def effective_feed(feed: FeedConfig, postal_code: str) -> FeedConfig:
    try:
        codes = parse_postal_codes(postal_code)
    except ValueError:
        codes = []
    if not codes:
        return feed
    return feed.model_copy(update={"options": {**feed.options, "postal_codes": ",".join(codes)}})
