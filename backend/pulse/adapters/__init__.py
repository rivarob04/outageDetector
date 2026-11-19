"""Feed adapter registry: adapter kind -> parse(content, feed) -> FeedSnapshot."""

from __future__ import annotations

import importlib

from pulse.adapters.base import FeedParseError, FeedSnapshot, Parser
from pulse.catalog.schema import FeedConfig

_MODULES = {
    "statuspage": "pulse.adapters.statuspage",
    "statusio": "pulse.adapters.statusio",
    "aws": "pulse.adapters.aws",
    "gcp": "pulse.adapters.gcp",
    "gworkspace": "pulse.adapters.gcp",
    "slack": "pulse.adapters.slack",
    "apple": "pulse.adapters.apple",
    "heroku": "pulse.adapters.heroku",
    "rss": "pulse.adapters.rss",
    "mymaps": "pulse.adapters.mymaps",
}
_FUNC = {"gworkspace": "parse_workspace"}


def get_parser(kind: str) -> Parser | None:
    module_name = _MODULES.get(kind)
    if module_name is None:
        return None
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError:
        return None
    fn: Parser | None = getattr(module, _FUNC.get(kind, "parse"), None)
    return fn


def parse_feed(content: bytes, feed: FeedConfig) -> FeedSnapshot:
    parser = get_parser(feed.adapter)
    if parser is None:
        raise FeedParseError(f"no adapter for '{feed.adapter}'")
    return parser(content, feed)


__all__ = ["FeedParseError", "FeedSnapshot", "get_parser", "parse_feed"]
