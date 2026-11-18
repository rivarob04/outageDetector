# CLAUDE.md: working agreement

Pulse is a self-hosted outage map / down detector. The source of truth is `docs/SPEC.md`, then `docs/SPEC-REVIEW.md` (which overrides the spec where they differ), then `docs/ARCHITECTURE.md`.

## Stack
- Backend: Python 3.12+, FastAPI, httpx (async), SQLAlchemy Core (async; aiosqlite / asyncpg), Alembic, Pydantic v2, Typer CLI. Package: `backend/pulse`.
- Frontend: React 18 + Vite + TypeScript + Tailwind v4, react-router, maplibre-gl. Located in `frontend/`. Built into `frontend/dist`, which the backend serves.
- Deploy: one multi-stage `Dockerfile` (multi-arch) and `deploy/docker-compose.yml`.

## Commands
| Task | Command |
|---|---|
| Install backend deps | `cd backend && uv sync --extra dev` |
| Run backend (dev) | `make dev-backend` (uvicorn with reload, port 8080) |
| Run frontend (dev) | `make dev-frontend` (Vite on 5173, proxies `/api` to 8080) |
| Demo mode | `PULSE_DEMO=1 make dev-backend` |
| Tests | `make test` (backend pytest + frontend vitest) |
| Lint / typecheck | `make lint` (ruff, mypy, eslint, tsc) |
| Format | `make fmt` |
| Build image | `make build` |
| Compose up / down | `make up` / `make down` |
| Verify catalog | `pulse catalog verify` (needs internet) |

## Rules
1. **No network in unit tests.** Use recorded fixtures in `backend/tests/fixtures/` and `httpx.MockTransport` / `respx`. Mark any test that needs the network with `@pytest.mark.live`. Those tests are skipped by default.
2. **Verify endpoints live before adding them to the catalog.** Run `pulse catalog verify --only <id>` or let the `catalog-verify` workflow do it. Never commit a status URL from memory as `verified: true`.
3. **The verdict engine is pure.** `pulse/verdict/engine.py` does no I/O and reads no clock (time is passed in). Every rule change needs a table-driven test and an update to `docs/verdict.md`.
4. **Be a polite client.** Every outbound request goes through `pulse/http.py`, which handles the User-Agent, timeouts, per-host rate limits and conditional requests.
5. **Update the docs and `CHANGELOG.md` (Unreleased section) with every change.** README claims must be true.
6. **Conventional commits**: `feat:`, `fix:`, `docs:`, `test:`, `chore:`, `refactor:`, `ci:`.
7. Secrets only come from env vars or Docker secrets (`*_FILE`). Never log them.
8. Keep the idle footprint small. No new heavy dependencies without justification in the PR.

## Code style
- Python: ruff (lint + format, line length 100), mypy in strict-ish mode for `pulse/verdict`, `pulse/adapters` and `pulse/probes`. Type hints everywhere. Prefer dataclasses/Pydantic models over dicts at boundaries.
- TypeScript: strict mode, function components, no `any` without a comment. Shared API types live in `frontend/src/api/types.ts`.
- UI: status is never conveyed by color alone (pair it with an icon or shape).
