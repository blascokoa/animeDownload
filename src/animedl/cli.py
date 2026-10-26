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
from animedl.naming import NameParts, safe_name
from animedl.progress import LogProgress, RichProgress
from animedl.scraping import find_player_links
from animedl.service import AnimeService, Report, rank_sources
from animedl.sites import SiteError, extractor_for

app = typer.Typer(
    help="Descarga series de anime (animeflv, jkanime, animeonline.ninja...) y vigila los "
    "episodios nuevos.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)
console = Console()
err_console = Console(stderr=True)
log = logging.getLogger("animedl")

INTERRUPTED = (
    "[yellow]Interrumpido.[/yellow] Los episodios a medias vuelven a «pendiente» (sin contar como "
    "intento fallido) y empezarán de cero: los bajará el daemon si está en marcha, o la próxima "
    "vez que ejecutes `download`. Para no descargarlos: `mark <serie> --as skipped -e <n>`."
)


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
            console=err_console, show_path=False, rich_tracebacks=True
        )
        fmt = "%(message)s"
    else:
        handler = logging.StreamHandler()
        fmt = "%(asctime)s %(levelname)-7s %(message)s"
    logging.basicConfig(level=logging.WARNING, format=fmt, handlers=[handler], force=True)
    logging.getLogger("animedl").setLevel(level)
    # "skipped: maximum number of running instances" is expected while the queue downloads
    logging.getLogger("apscheduler").setLevel(logging.INFO if verbose else logging.ERROR)


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
def open_service(*, dump_dir: Path | None = None):
    settings = state.settings
    db = Database(settings.database)
    progress = RichProgress(err_console) if err_console.is_terminal else LogProgress()
    try:
        http = HttpClient(settings, dump_dir=dump_dir)
        yield AnimeService(settings, db, http, Downloader(settings, progress=progress))
    except KeyboardInterrupt:
        console.print(INTERRUPTED)
        raise typer.Exit(130) from None
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


def describe(series: Series) -> str:
    """How Plex/Jellyfin will see the series: "Black Clover (2017) {tmdb-73223} · temporada 2"."""
    parts = NameParts(series.folder, series.year, series.id_tag)
    text = f"{parts.series}{parts.ids}"
    if series.is_movie:
        return f"{text} · película"
    if series.season_override is not None:
        text += f" · temporada {series.season_override}"
    if series.episode_offset:
        text += f" · episodios +{series.episode_offset}"
    return text


def print_destination(service: AnimeService, series: Series) -> None:
    series = service.db.get_series(series.id)
    episodes = service.db.episodes(series.id)
    if episodes:
        target = service.episode_path(series, episodes[0])
        where = target if series.is_movie else target.parent
        console.print(f"  Plex/Jellyfin: {describe(series)}  →  {where}", soft_wrap=True)
    if service.settings.identify and not series.id_tag:
        console.print(
            "  [yellow]Sin ID de TMDB/TVDB:[/yellow] Plex/Jellyfin la buscarán por el título. Si no "
            f"la reconocen: `animedl identify {series.id} --anilist <ID de anilist.co/anime/ID>`"
        )


def print_moves(service: AnimeService, moves, *, dry_run: bool) -> None:
    for old, new in moves:
        console.print(f"  {old}  →  {new}", soft_wrap=True)
    if moves:
        verb = "se moverían" if dry_run else "movidos"
        console.print(f"  {len(moves)} fichero(s) {verb}")


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
    background: Annotated[
        bool,
        typer.Option(
            "--background",
            "-b",
            "--no-download",
            help="Solo registrar; el daemon la descarga en segundo plano",
        ),
    ] = False,
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
            print_destination(service, series)
            added.append(series)
        if added and background:
            console.print(
                "Queda en la cola: si el daemon está en marcha (`docker compose up -d`) empezará a "
                "descargarla en menos de un minuto. Progreso: `docker compose logs -f`."
            )
        elif added:
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
                f"{s.title}\n[dim]{describe(s)}[/dim]",
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
            "Temp.",
            "Ep.",
            "Estado",
            "Intentos",
            "Servidor",
            "Fichero / error",
            title=f"{s.title} → {describe(s)}",
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
    season: Annotated[
        int | None, typer.Option(help="Temporada (0 = especiales, -1 = la que diga la web)")
    ] = None,
    year: Annotated[int | None, typer.Option(help="Año para el nombre (0 = quitarlo)")] = None,
    media_id: Annotated[
        str | None,
        typer.Option(
            "--id", help="ID para Plex/Jellyfin: tmdb-123, tvdb-123 o imdb-tt123 ('' = quitarlo)"
        ),
    ] = None,
    movie: Annotated[
        bool | None, typer.Option("--movie/--tv", help="Tratarla como película o como serie")
    ] = None,
    episode_offset: Annotated[
        int | None, typer.Option(help="Sumar N al número de episodio de la web")
    ] = None,
) -> None:
    """Cambia la configuración de una serie (y mueve sus ficheros si cambia el nombre)."""
    with open_service() as service:
        s = resolve_series(service, series)
        values: dict = {}
        if url:
            values["url"] = service.extractor(url).normalize_url(url)
            values["site"] = service.extractor(url).name
        if name:
            values["folder"] = safe_name(name)
        if monitor:
            values["monitor"] = monitor
        if season is not None:
            values["season_override"] = None if season < 0 else season
        if year is not None:
            values["year"] = year or None
        if media_id is not None:
            tag = media_id.strip().strip("{}[]")
            if tag and not re.fullmatch(r"(tmdb|tvdb|imdb)-(tt)?\d+", tag):
                raise typer.BadParameter("Formato: tmdb-123, tvdb-123 o imdb-tt123")
            values["id_tag"] = f"{{{tag}}}" if tag else None
        if movie is not None:
            values["media_kind"] = "movie" if movie else "tv"
        if episode_offset is not None:
            values["episode_offset"] = episode_offset
        service.db.update_series(s.id, **values)
        console.print(f"Actualizada «{s.title}»: {', '.join(values) or 'sin cambios'}")
        s = service.db.get_series(s.id)
        console.print(f"  Plex/Jellyfin: {describe(s)}")
        print_moves(service, service.rename_files(s), dry_run=False)


@app.command()
def identify(
    series: Annotated[list[str] | None, typer.Argument(help="Series (todas si se omite)")] = None,
    anilist: Annotated[
        int | None, typer.Option(help="ID de AniList, el número de anilist.co/anime/<ID>")
    ] = None,
    keep_name: Annotated[
        bool, typer.Option("--keep-name", help="No cambiar el nombre de la carpeta")
    ] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Solo mostrar qué se movería")] = False,
) -> None:
    """Identifica la serie (AniList + TMDB/TVDB) y renombra sus ficheros para Plex/Jellyfin."""
    with open_service() as service:
        targets = (
            [resolve_series(service, ref) for ref in series] if series else service.db.list_series()
        )
        if anilist and len(targets) != 1:
            raise typer.BadParameter("--anilist solo se puede usar con una serie")
        for s in targets:
            media = service.identify_series(s, anilist_id=anilist, keep_name=keep_name)
            s = service.db.get_series(s.id)
            status = "" if media else " [yellow](no encontrada en AniList)[/yellow]"
            console.print(f"[bold]{s.title}[/bold] → {describe(s)}{status}", soft_wrap=True)
            print_moves(service, service.rename_files(s, dry_run=dry_run), dry_run=dry_run)


@app.command()
def rename(
    series: Annotated[list[str] | None, typer.Argument(help="Series (todas si se omite)")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Solo mostrar qué se movería")] = False,
) -> None:
    """Mueve lo ya descargado al formato de Plex/Jellyfin (identifica antes las series que aún no
    lo estén)."""
    with open_service() as service:
        targets = (
            [resolve_series(service, ref) for ref in series] if series else service.db.list_series()
        )
        for s in targets:
            if service.settings.identify and not s.identified_at:
                service.identify_series(s)
                s = service.db.get_series(s.id)
            moves = service.rename_files(s, dry_run=dry_run)
            if moves:
                console.print(f"[bold]{s.title}[/bold] → {describe(s)}")
                print_moves(service, moves, dry_run=dry_run)
        console.print("Simulación: no se ha movido nada." if dry_run else "Hecho.")


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
def dump(
    url: Annotated[str, typer.Argument(help="Página a guardar (serie o episodio)")],
    out: Annotated[Path, typer.Option(help="Carpeta donde guardar el HTML")] = Path("debug"),
    depth: Annotated[int, typer.Option(help="Niveles de reproductores/iframes a seguir")] = 2,
) -> None:
    """Guarda una página y los reproductores que enlaza, para adaptar animedl a una web nueva."""
    http = HttpClient(state.settings, dump_dir=out)
    queue: list[tuple[str, str | None, int]] = [(url, None, 0)]
    seen: set[str] = set()
    while queue and len(seen) < 30:
        page, referer, level = queue.pop(0)
        if page in seen:
            continue
        seen.add(page)
        try:
            resp = http.get(page, referer=referer)
        except FetchError as exc:
            console.print(f"[red]✗[/red] {page}: {exc}", soft_wrap=True)
            continue
        console.print(f"[green]✓[/green] HTTP {resp.status_code} {page}", soft_wrap=True)
        if level < depth:
            for link, _ in find_player_links(resp.text, str(resp.url)):
                queue.append((link, str(resp.url), level + 1))
    console.print(f"{len(seen)} página(s) guardadas en {out}")


@app.command()
def daemon(
    cron: Annotated[
        str | None, typer.Option(help="Expresión cron (por defecto la de la config)")
    ] = None,
    run_now: Annotated[
        bool, typer.Option("--run-now", help="Ejecutar también al arrancar")
    ] = False,
) -> None:
    """Se queda en marcha: `update` según el calendario (semanal por defecto) y, cada minuto,
    descarga lo que haya pendiente (p. ej. series añadidas con `add --background`)."""
    from animedl.scheduler import run_daemon

    def job() -> None:
        log.info("Comprobando episodios nuevos...")
        with open_service() as service:
            print_report(service.update())

    settings = state.settings
    run_daemon(
        job,
        cron or settings.schedule,
        run_now=run_now,
        queue_job=process_queue,
        queue_interval=settings.queue_interval,
    )


def process_queue() -> None:
    """Download pending episodes (failed ones wait for the weekly check)."""
    with open_service() as service:
        report = service.download_pending(service.db.list_series(), include_failed=False)
        if report.downloaded or report.failed or report.errors:
            print_report(report)


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
