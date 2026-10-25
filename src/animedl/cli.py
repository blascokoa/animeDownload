"""Command line interface."""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from animedl import __version__, hosts
from animedl.config import Settings, load_settings
from animedl.db import Database, Series
from animedl.downloader import Downloader
from animedl.http import FetchError, HttpClient
from animedl.models import EpisodeRef, EpisodeStatus, Monitor, format_number
from animedl.service import AnimeService, Report, rank_sources
from animedl.sites import SiteError, extractor_for

app = typer.Typer(
    help="Descarga series de anime (animeflv, jkanime, animeonline.ninja...) y vigila los "
    "episodios nuevos.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)
console = Console()
log = logging.getLogger("animedl")


class State:
    config: Path | None = None
    verbose: bool = False
    _settings: Settings | None = None

    @property
    def settings(self) -> Settings:
        if self._settings is None:
            self._settings = load_settings(self.config)
        return self._settings


state = State()


def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    if sys.stderr.isatty():
        handler: logging.Handler = RichHandler(
            console=Console(stderr=True), show_path=False, rich_tracebacks=True
        )
        fmt = "%(message)s"
    else:
        handler = logging.StreamHandler()
        fmt = "%(asctime)s %(levelname)-7s %(message)s"
    logging.basicConfig(level=logging.WARNING, format=fmt, handlers=[handler], force=True)
    logging.getLogger("animedl").setLevel(level)
    logging.getLogger("apscheduler").setLevel(logging.INFO if verbose else logging.WARNING)


@app.callback()
def main(
    config: Annotated[
        Path | None, typer.Option("--config", "-c", help="Fichero config.toml")
    ] = None,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Más detalle en los logs")
    ] = False,
) -> None:
    state.config = config
    state.verbose = verbose
    setup_logging(verbose)


@contextmanager
def open_service(*, progress: bool = True, dump_dir: Path | None = None):
    settings = state.settings
    db = Database(settings.database)
    try:
        http = HttpClient(settings, dump_dir=dump_dir)
        downloader = Downloader(settings, progress=progress and sys.stdout.isatty())
        yield AnimeService(settings, db, http, downloader)
    finally:
        db.close()


def resolve_series(service: AnimeService, ref: str) -> Series:
    found = service.db.find_series(ref)
    if not found:
        console.print(f"[red]No hay ninguna serie que coincida con «{ref}»[/red]")
        raise typer.Exit(1)
    if len(found) > 1:
        console.print(f"[yellow]«{ref}» coincide con varias series, usa el id:[/yellow]")
        for s in found:
            console.print(f"  {s.id}: {s.title}")
        raise typer.Exit(1)
    return found[0]


def parse_ranges(text: str) -> Callable[[float], bool]:
    """ "1-5,8,10.5" -> predicate on episode numbers."""
    ranges: list[tuple[float, float]] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)?", part)
        if m:
            ranges.append((float(m.group(1)), float(m.group(2)) if m.group(2) else float("inf")))
        elif re.fullmatch(r"\d+(?:\.\d+)?", part):
            ranges.append((float(part), float(part)))
        else:
            raise typer.BadParameter(f"Rango no válido: «{part}» (ejemplo: 1-5,8,12-)")
    return lambda n: any(lo <= n <= hi for lo, hi in ranges)


def print_report(report: Report) -> None:
    for series, ep in report.new_episodes:
        console.print(f"[cyan]Nuevo[/cyan] {series.title} - episodio {format_number(ep.number)}")
    for series, ep, path in report.downloaded:
        console.print(
            f"[green]OK[/green]    {series.title} - episodio {format_number(ep.number)} → {path}"
        )
    for series, ep, error in report.failed:
        console.print(
            f"[red]Fallo[/red] {series.title} - episodio {format_number(ep.number)}: {error}",
            soft_wrap=True,
        )
    for error in report.errors:
        console.print(f"[red]Error[/red] {error}", soft_wrap=True)
    console.print(
        f"Episodios nuevos: {len(report.new_episodes)} · descargados: {len(report.downloaded)}"
        f" · fallidos: {len(report.failed)}"
    )


# --------------------------------------------------------------------- commands


@app.command()
def add(
    urls: Annotated[list[str], typer.Argument(help="URL(s) de la serie")],
    no_download: Annotated[bool, typer.Option("--no-download", help="Solo registrar")] = False,
    monitor: Annotated[Monitor, typer.Option(help="Cuándo buscar episodios nuevos")] = Monitor.AUTO,
    season: Annotated[int | None, typer.Option(help="Forzar número de temporada")] = None,
    name: Annotated[str | None, typer.Option(help="Nombre de la carpeta")] = None,
    from_episode: Annotated[
        float | None,
        typer.Option("--from-episode", help="Ignorar episodios anteriores a este número"),
    ] = None,
) -> None:
    """Registra una serie y descarga sus episodios."""
    with open_service() as service:
        added: list[Series] = []
        errors = 0
        for url in urls:
            try:
                series, new = service.add_series(
                    url, monitor=monitor, season=season, name=name, from_episode=from_episode
                )
            except (SiteError, FetchError) as exc:
                console.print(f"[red]Error con {url}:[/red] {exc}", soft_wrap=True)
                errors += 1
                continue
            counts = service.db.episode_counts().get(series.id, {})
            console.print(
                f"[bold]{series.title}[/bold] (id {series.id}, {series.site}, {series.status}): "
                f"{sum(counts.values())} episodios, {len(new)} nuevos, "
                f"{counts.get('done', 0)} ya descargados"
            )
            added.append(series)
        if added and not no_download:
            print_report(service.download_pending(added))
        if errors:
            raise typer.Exit(1)


@app.command("list")
def list_series() -> None:
    """Lista las series registradas."""
    with open_service() as service:
        counts = service.db.episode_counts()
        table = Table(
            "id",
            "Serie",
            "Web",
            "Estado",
            "Vigilar",
            "Descargados",
            "Pendientes",
            "Fallidos",
            "Última comprobación",
        )
        for s in service.db.list_series():
            c = counts.get(s.id, {})
            total = sum(c.values()) - c.get("skipped", 0)
            table.add_row(
                str(s.id),
                s.title,
                s.site,
                s.status.value,
                f"{s.monitor.value}{' ✓' if s.should_check else ''}",
                f"{c.get('done', 0)}/{total}",
                str(c.get("pending", 0) + c.get("downloading", 0)),
                str(c.get("failed", 0)),
                (s.last_checked_at or "-")[:16].replace("T", " "),
            )
        console.print(table)


@app.command()
def episodes(
    series: Annotated[str, typer.Argument(help="id, URL o parte del título")],
    status: Annotated[list[EpisodeStatus] | None, typer.Option(help="Filtrar por estado")] = None,
) -> None:
    """Muestra los episodios de una serie."""
    with open_service() as service:
        s = resolve_series(service, series)
        table = Table(
            "Temp.", "Ep.", "Estado", "Intentos", "Servidor", "Fichero / error", title=s.title
        )
        for ep in service.db.episodes(s.id, status):
            detail = ep.file_path if ep.status == EpisodeStatus.DONE else (ep.last_error or "")
            style = {"done": "green", "failed": "red", "skipped": "dim"}.get(ep.status.value)
            table.add_row(
                str(s.season_override or ep.season),
                format_number(ep.number),
                ep.status.value,
                str(ep.attempts),
                ep.server or "",
                detail[:120],
                style=style,
            )
        console.print(table)


@app.command()
def download(
    series: Annotated[list[str] | None, typer.Argument(help="Series (todas si se omite)")] = None,
    episodes_: Annotated[
        str | None, typer.Option("--episodes", "-e", help="Rango, ej. 1-5,8,12-")
    ] = None,
    retry_failed: Annotated[
        bool,
        typer.Option("--retry-failed", help="Reintentar también los que agotaron los intentos"),
    ] = False,
) -> None:
    """Descarga los episodios pendientes."""
    with open_service() as service:
        targets = (
            [resolve_series(service, ref) for ref in series] if series else service.db.list_series()
        )
        pred = parse_ranges(episodes_) if episodes_ else None
        report = service.download_pending(
            targets,
            episode_filter=(lambda ep: pred(ep.number)) if pred else None,
            retry_failed=retry_failed,
        )
        print_report(report)


@app.command()
def update(
    check_all: Annotated[
        bool, typer.Option("--all", help="Comprobar también las finalizadas")
    ] = False,
    no_download: Annotated[bool, typer.Option("--no-download", help="Solo buscar")] = False,
) -> None:
    """Busca episodios nuevos de las series en emisión y descarga lo pendiente."""
    with open_service() as service:
        print_report(service.update(check_all=check_all, download=not no_download))


@app.command()
def edit(
    series: Annotated[str, typer.Argument(help="id, URL o parte del título")],
    url: Annotated[str | None, typer.Option(help="Nueva URL (si la web cambia de dominio)")] = None,
    name: Annotated[str | None, typer.Option(help="Nuevo nombre de carpeta")] = None,
    monitor: Annotated[Monitor | None, typer.Option(help="auto / always / never")] = None,
    season: Annotated[int | None, typer.Option(help="Forzar temporada (0 = la de la web)")] = None,
) -> None:
    """Cambia la configuración de una serie."""
    with open_service() as service:
        s = resolve_series(service, series)
        values: dict = {}
        if url:
            values["url"] = service.extractor(url).normalize_url(url)
            values["site"] = service.extractor(url).name
        if name:
            values["folder"] = name
        if monitor:
            values["monitor"] = monitor
        if season is not None:
            values["season_override"] = season or None
        service.db.update_series(s.id, **values)
        console.print(f"Actualizada «{s.title}»: {', '.join(values) or 'sin cambios'}")


@app.command()
def mark(
    series: Annotated[str, typer.Argument(help="id, URL o parte del título")],
    as_: Annotated[EpisodeStatus, typer.Option("--as", help="Nuevo estado")],
    episodes_: Annotated[str, typer.Option("--episodes", "-e", help="Rango, ej. 1-12")] = "1-",
) -> None:
    """Marca episodios a mano (p. ej. como 'skipped' si ya los tienes, o 'pending' para repetir)."""
    with open_service() as service:
        s = resolve_series(service, series)
        pred = parse_ranges(episodes_)
        ids = [ep.id for ep in service.db.episodes(s.id) if pred(ep.number)]
        n = service.db.set_status(ids, as_, reset_attempts=as_ == EpisodeStatus.PENDING)
        console.print(f"{n} episodio(s) marcados como {as_.value}")


@app.command()
def remove(
    series: Annotated[str, typer.Argument(help="id, URL o parte del título")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="No pedir confirmación")] = False,
) -> None:
    """Deja de seguir una serie (los ficheros descargados no se borran)."""
    with open_service() as service:
        s = resolve_series(service, series)
        if not yes and not typer.confirm(f"¿Quitar «{s.title}» de la base de datos?"):
            raise typer.Abort()
        service.db.delete_series(s.id)
        console.print(f"«{s.title}» eliminada (los vídeos siguen en el disco)")


@app.command()
def probe(
    url: Annotated[str, typer.Argument(help="URL de la serie")],
    episode: Annotated[
        float | None,
        typer.Option("--episode", "-e", help="Mostrar los servidores de este episodio"),
    ] = None,
    resolve: Annotated[bool, typer.Option(help="Resolver los enlaces de vídeo")] = False,
    dump: Annotated[
        Path | None, typer.Option(help="Guardar el HTML descargado en esta carpeta")
    ] = None,
) -> None:
    """Diagnóstico: muestra lo que se extrae de una URL sin tocar la base de datos."""
    settings = state.settings
    http = HttpClient(settings, dump_dir=dump)
    extractor = extractor_for(url, http)
    info = extractor.get_series(extractor.normalize_url(url))
    console.print(
        f"[bold]{info.title}[/bold] · extractor {extractor.name} · estado {info.status}"
        f" · {len(info.episodes)} episodios"
    )
    eps = info.episodes
    shown = eps if len(eps) <= 10 else [*eps[:5], None, *eps[-5:]]
    for ep in shown:
        console.print(
            "  ..." if ep is None else f"  T{ep.season} E{format_number(ep.number)}: {ep.url}"
        )
    if episode is None:
        return
    ref: EpisodeRef | None = next((e for e in eps if e.number == episode), None)
    if ref is None:
        console.print(f"[red]No existe el episodio {format_number(episode)}[/red]")
        raise typer.Exit(1)
    sources = extractor.get_sources(ref)
    ranked = rank_sources(sources, settings.languages, settings.servers)
    table = Table(
        "#", "Servidor", "Idioma", "Etiqueta", "URL", title="Servidores (en orden de uso)"
    )
    for i, src in enumerate(ranked, 1):
        table.add_row(str(i), src.server, src.lang or "?", src.label or "", src.url)
    console.print(table)
    used = {s.url for s in ranked}
    ignored = {s.url: s for s in sources if s.url not in used}.values()
    if ignored:
        console.print("Ignorados: " + ", ".join(f"{s.server}/{s.lang or '?'}" for s in ignored))
    if resolve:
        for src in ranked:
            try:
                streams = hosts.resolve(http, src)
            except Exception as exc:
                console.print(f"[red]✗ {src.server}[/red]: {exc}", soft_wrap=True)
                continue
            for st in streams:
                console.print(f"[green]✓ {src.server}[/green] [{st.kind}] {st.url}", soft_wrap=True)


@app.command()
def daemon(
    cron: Annotated[
        str | None, typer.Option(help="Expresión cron (por defecto la de la config)")
    ] = None,
    run_now: Annotated[
        bool, typer.Option("--run-now", help="Ejecutar también al arrancar")
    ] = False,
) -> None:
    """Se queda en marcha y ejecuta `update` según el calendario (semanal por defecto)."""
    from animedl.scheduler import run_daemon

    def job() -> None:
        log.info("Comprobando episodios nuevos...")
        with open_service(progress=False) as service:
            print_report(service.update())

    run_daemon(job, cron or state.settings.schedule, run_now=run_now)


@app.command()
def config() -> None:
    """Muestra la configuración efectiva."""
    from dataclasses import asdict

    for key, value in asdict(state.settings).items():
        console.print(f"{key} = {value!r}")


@app.command()
def version() -> None:
    """Muestra la versión."""
    console.print(__version__)


USER_ERRORS = (SiteError, FetchError, FileNotFoundError, ValueError)


def run() -> None:
    """Entry point: known errors are shown as a message instead of a traceback."""
    try:
        app()
    except USER_ERRORS as exc:
        if state.verbose:
            raise
        console.print(f"[red]Error:[/red] {exc}", soft_wrap=True)
        raise SystemExit(1) from None
