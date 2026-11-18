"""`pulse` command-line interface."""

from __future__ import annotations

import typer
import uvicorn

from pulse.config import get_settings

app = typer.Typer(help="Pulse: self-hosted outage map", no_args_is_help=True)


@app.command()
def serve(host: str | None = None, port: int | None = None, reload: bool = False) -> None:
    """Run the hub (API, UI, scheduler)."""
    s = get_settings()
    uvicorn.run(
        "pulse.app:create_app",
        factory=True,
        host=host or s.host,
        port=port or s.port,
        reload=reload,
        log_config=None,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


@app.command()
def migrate() -> None:
    """Apply database migrations."""
    from pulse.db.migrate import upgrade

    upgrade(get_settings())
    typer.echo("migrations applied")


@app.command()
def version() -> None:
    from pulse import __version__

    typer.echo(__version__)


if __name__ == "__main__":
    app()
