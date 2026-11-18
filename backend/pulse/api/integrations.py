"""Registers the integrations' HTTP routes (metrics, feeds, badges, status page)."""

from __future__ import annotations

from fastapi import FastAPI

from pulse.integrations import outputs


def register(app: FastAPI) -> None:
    outputs.register(app)
