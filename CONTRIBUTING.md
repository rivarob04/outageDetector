# Contributing

Thanks for helping! Start with `CLAUDE.md` (the working agreement) and `docs/ARCHITECTURE.md`.

## Set up
```bash
make install          # backend (uv) + frontend (npm)
make dev-backend      # API on :8080 (try PULSE_DEMO=1 for fake data)
make dev-frontend     # UI on :5173 with hot reload
make test lint        # before every PR
```

## Ground rules
- **No network in unit tests.** Record real payloads as fixtures. Live tests need `@pytest.mark.live`.
- **Verify every catalog endpoint live** before adding it (see docs/ADDING_A_PROVIDER.md).
- **The verdict engine is pure.** Every rule change updates `docs/verdict.md` and adds a table-driven test.
- Status is never conveyed by color alone in the UI.
- Conventional commit messages (`feat:`, `fix:`, `docs:` …) and a CHANGELOG entry under *Unreleased*.

## Adding a service
The quickest contribution: see docs/ADDING_A_PROVIDER.md. If you just want a service added, open a **Request a service** issue with its status page URL.
