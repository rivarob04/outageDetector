# Changelog

All notable changes to this project are documented here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versioning: [SemVer](https://semver.org/).

## [Unreleased]

### Added
- Phase 3: HTTP probes with DNS/connect/TLS/TTFB timing, DNS probes comparing the system resolver with public resolvers, TCP connect and TLS-expiry probes, an SSRF guard (also applied to redirects) for user-supplied targets, connectivity canaries, latency baselines, an asyncio scheduler (jitter, bounded concurrency, backoff, no overlap) and the pure verdict engine (states, scopes, reasons, flap protection) with a decision table in docs/verdict.md and 41 table-driven tests.
- Phase 2: status-feed adapters (Atlassian Statuspage, status.io, AWS Health, Google Cloud, Google Workspace, Slack, Apple, Heroku, RSS/Atom); a polite HTTP client (UA, per-host spacing, retries, ETag/Last-Modified); a 53-service catalog validated by schema, with 165 map regions; `pulse catalog verify` and a weekly **Catalog verify** workflow that records live payloads as contract-test fixtures (52/53 verified on first run).
- Phase 1: FastAPI backend skeleton (settings, JSON logging, /healthz, /readyz, Alembic), React + Vite + Tailwind UI skeleton, multi-stage multi-arch Dockerfile, docker compose with postgres/mqtt/agent profiles, Makefile, CI (lint, typecheck, tests on SQLite + Postgres, compose smoke test) and GHCR release workflow.
- Phase 0: product spec, spec review, architecture, roadmap and the CLAUDE.md working agreement.
