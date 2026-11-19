"""Per-installation feed resolution (e.g. local ISP outage lookups by postal code).

Catalog feeds are static; a few providers only publish outages per postal code. Those feeds
declare ``options.postal_url`` with a ``{postal_code}`` placeholder. When the user has set
``PULSE_POSTAL_CODE``, the effective feed uses that URL and carries the normalized code in
``options.postal_code`` so the (pure) adapter can filter by area.
"""

from __future__ import annotations

import re

from pulse.catalog.schema import FeedConfig

# Canada Post: letter-digit-letter digit-letter-digit; D, F, I, O, Q, U never appear,
# and W, Z never start a code.
_POSTAL = re.compile(r"^[ABCEGHJ-NPRSTVXY]\d[ABCEGHJ-NPRSTV-Z]\d[ABCEGHJ-NPRSTV-Z]\d$")


def normalize_postal_code(value: str) -> str:
    """Return ``"N8W1H4"`` for ``" n8w 1h4 "``; ``""`` stays empty. Raises on invalid codes."""
    code = re.sub(r"[\s-]", "", value or "").upper()
    if not code:
        return ""
    if not _POSTAL.match(code):
        raise ValueError("postal_code must be a Canadian postal code like N8W 1H4")
    return code


def effective_feed(feed: FeedConfig, postal_code: str) -> FeedConfig:
    try:
        code = normalize_postal_code(postal_code)
    except ValueError:
        code = ""
    if not code:
        return feed
    options = {**feed.options, "postal_code": code}
    url = feed.url
    template = feed.options.get("postal_url")
    if template:
        url = template.replace("{postal_code}", code)
    return feed.model_copy(update={"url": url, "options": options})
