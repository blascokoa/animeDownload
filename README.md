# animedl

Pegas la URL de una serie y `animedl` busca todos sus episodios, elige un servidor que
funcione y los descarga a tu disco duro con nombres que entienden Plex y Jellyfin. Guarda en
una base de datos SQLite qué tienes y qué no. Una vez por semana revisa las series que siguen
en emisión y descarga los episodios nuevos.

```
/mnt/disco/anime/
└── Black Clover 2nd Season/
    └── Season 01/
        ├── Black Clover 2nd Season - S01E01.mp4
        ├── Black Clover 2nd Season - S01E02.mp4
        └── ...
```

## Webs soportadas

| Web | Ejemplo | Cómo se leen los episodios |
| --- | --- | --- |
| AnimeFLV y clones (`animeflv.ba`, `animeflv.net`…) | `https://animeflv.ba/anime/the-promised-neverland-mrwxzt` | variables `anime_info`/`episodes`/`videos` de la página; si no están, enlaces `/ver/…` |
| JKAnime | `https://jkanime.net/black-clover-2nd-season/` | API AJAX de episodios (la nueva y la antigua); si no, enlaces de la página |
| AnimeOnline.ninja | `https://ww3.animeonline.ninja/online/kanojo-no-tomodachi/` | tema DooPlay de WordPress (temporadas y API del reproductor) |
| Cualquier otra | — | extractor genérico (enlaces de episodios + iframes); puede o no funcionar |

**Servidores de vídeo:** ok.ru, Streamwish, VidHide/Filelions, Filemoon, Mp4upload,
YourUpload, los reproductores propios de JKAnime, Streamtape, Voe, Doodstream, Mixdrop y
MEGA. Por cada episodio se prueban por orden de preferencia (idioma y servidor) hasta que uno
funciona.

> ⚠️ **Estado:** las tres webs estaban bloqueadas en el entorno donde se escribió esto, así que
> los extractores siguen la estructura conocida de cada web pero **aún no se han probado contra
> las páginas reales**. La descarga (yt-dlp, HLS, base de datos, programación) sí está probada
> de principio a fin contra una web falsa en local. Empieza con `animedl probe` (ver
> [Diagnóstico](#diagnóstico)); si algo no cuadra, ese comando te da lo necesario para
> arreglarlo.

## Instalación

### Opción A: Docker (recomendada para dejarlo funcionando solo)

```bash
cp .env.example .env        # pon tu carpeta del disco en ANIME_DIR y tu usuario en PUID/PGID
mkdir -p data               # aquí vive la base de datos
docker compose up -d --build
```

El contenedor se queda en marcha y comprueba las series en emisión cada lunes a las 04:00
(`ANIMEDL_SCHEDULE` en `.env`). Para darle órdenes:

```bash
docker compose run --rm animedl add https://jkanime.net/black-clover-2nd-season/
docker compose run --rm animedl list
docker compose logs -f      # ver qué hace el programador
```

### Opción B: directamente en tu ordenador

Necesitas Python 3.11+, [`ffmpeg`](https://ffmpeg.org/) y, si quieres enlaces de MEGA,
[`megatools`](https://megatools.megous.com/) (`sudo apt install ffmpeg megatools`).

```bash
uv tool install .          # o: pipx install .
animedl --help
```

Para desarrollar: `uv sync` y luego `uv run animedl ...`.

## Uso

```bash
# Añadir una serie y descargar todo lo que tenga
animedl add https://animeflv.ba/anime/the-promised-neverland-mrwxzt

# Varias a la vez, solo registrarlas (descargar luego)
animedl add URL1 URL2 --no-download

# Ya tengo hasta el 100: registrar y descargar solo del 101 en adelante
animedl add https://jkanime.net/black-clover/ --from-episode 101

# Que la carpeta sea "Black Clover" Season 02 en vez de "Black Clover 2nd Season" Season 01
animedl add https://jkanime.net/black-clover-2nd-season/ --name "Black Clover" --season 2

animedl list                         # series, estado y cuántos episodios hay descargados
animedl episodes "black clover"      # detalle por episodio (acepta id, URL o parte del título)
animedl download                     # descargar todo lo pendiente
animedl download 3 -e 10-12          # solo los episodios 10 a 12 de la serie con id 3
animedl download --retry-failed      # reintentar los que agotaron sus intentos
animedl update                       # lo que hace la tarea semanal (ver abajo)
animedl mark 3 --as skipped -e 1-50  # no descargar esos episodios
animedl mark 3 --as pending -e 7     # volver a descargar el 7
animedl edit 3 --monitor always      # vigilarla aunque la web diga que terminó
animedl edit 3 --url https://animeflv.bz/anime/...   # la web cambió de dominio
animedl remove 3                     # dejar de seguirla (los vídeos no se borran)
animedl config                       # ver la configuración efectiva
```

Si añades una serie que ya existe, solo busca episodios nuevos. Si un episodio ya está en el
disco con el nombre esperado (con cualquier extensión de vídeo), se marca como descargado y no
se vuelve a bajar.

## Episodios nuevos cada semana

`animedl update` hace dos cosas:

1. Vuelve a leer la página de cada serie que hay que vigilar y registra los episodios nuevos.
   Por defecto (`--monitor auto`) se vigilan las que la web marca **en emisión** o con estado
   desconocido; las **finalizadas** se dejan de comprobar solas. Con `--monitor always` o
   `never` lo fuerzas.
2. Descarga todo lo pendiente, y reintenta los fallidos hasta `max_attempts` veces (5 por
   defecto). Si un episodio recién salido aún no tiene servidores, se reintenta a la semana
   siguiente.

Formas de programarlo (elige una):

- **Docker:** ya viene hecho. El contenedor ejecuta `animedl daemon`.
- **`animedl daemon`** en cualquier máquina: se queda en marcha y usa el cron de `schedule`
  (`--cron "0 4 * * 1"`, `--run-now` para lanzarlo también al arrancar).
- **systemd** (Linux; si el ordenador estaba apagado, se ejecuta al encender):
  ```bash
  mkdir -p ~/.config/systemd/user
  cp deploy/systemd/animedl-update.* ~/.config/systemd/user/
  systemctl --user daemon-reload
  systemctl --user enable --now animedl-update.timer
  ```
- **cron:** `0 4 * * 1  /home/TU_USUARIO/.local/bin/animedl update >> ~/animedl.log 2>&1`
- **Windows:** en el Programador de tareas, crea una tarea semanal que ejecute `animedl update`.

Si dos procesos coinciden (por ejemplo, el daemon y tú a mano), no pasa nada: cada episodio se
"reserva" en la base de datos antes de descargarse.

## Configuración

Copia `config.example.toml` a `config.toml`, o a `~/.config/animedl/config.toml`; con Docker,
a `data/config.toml`. Cada opción se puede dar también como variable de entorno
`ANIMEDL_<OPCIÓN>`. Las más útiles:

| Opción | Por defecto | Para qué |
| --- | --- | --- |
| `download_dir` | `~/Anime` | carpeta del disco duro |
| `database` | `~/.local/share/animedl/animedl.db` | base de datos SQLite |
| `languages` | `["sub", "lat", "cast"]` | versiones aceptadas, en orden de preferencia |
| `servers` | okru, streamwish, vidhide, … | orden en que se prueban los servidores |
| `schedule` | `0 4 * * 1` | cron de `animedl daemon` |
| `filename_template` | `{series}/Season {season:02d}/{series} - S{season:02d}E{episode}` | nombre de los ficheros |
| `cookies_file`, `user_agent` | — | para webs con Cloudflare (ver abajo) |

## Diagnóstico

`probe` muestra lo que se extrae de una URL sin tocar la base de datos:

```bash
animedl probe https://jkanime.net/black-clover-2nd-season/                 # título, estado, episodios
animedl probe https://jkanime.net/black-clover-2nd-season/ -e 1            # servidores del episodio 1
animedl probe https://jkanime.net/black-clover-2nd-season/ -e 1 --resolve  # y los enlaces de vídeo
animedl -v probe URL -e 1 --resolve --dump debug/                          # guarda el HTML en debug/
```

Problemas típicos:

- **«pide un desafío anti-bots (Cloudflare)»:** abre la web en tu navegador, exporta las
  cookies a `cookies.txt` (por ejemplo, con la extensión «Get cookies.txt LOCALLY») y
  configura `cookies_file` y `user_agent`, este último con el mismo User-Agent que ese
  navegador.
- **No encuentra episodios o servidores:** la web ha cambiado su HTML. Con `--dump` tienes la
  página guardada para ajustar el extractor en `src/animedl/sites/`.
- **Un servidor falla siempre:** quítalo de `servers` o bájalo en la lista. Si es un dominio
  nuevo de un servidor conocido, añade la palabra clave en `src/animedl/hosts/catalog.py`.
- **La web cambió de dominio:** `animedl edit ID --url NUEVA_URL`.

## Cómo está hecho

```
src/animedl/
├── cli.py          comandos (Typer)
├── service.py      añadir series, sincronizar, elegir servidor, descargar
├── db.py           SQLite: tablas series y episodes
├── sites/          un extractor por web (episodios + reproductores de cada episodio)
├── hosts/          de reproductor embebido a enlace de vídeo (desempaquetador P.A.C.K.E.R.,
│                   streamtape, voe, dood, mega...; el genérico cubre los basados en JWPlayer)
├── downloader.py   yt-dlp (mp4/HLS, comprueba duración con ffprobe) y megatools
├── http.py         curl_cffi imitando a Chrome, reintentos, cookies, detección de Cloudflare
└── scheduler.py    APScheduler para `animedl daemon`
```

Para añadir otra web, crea una subclase de `SiteExtractor` en `src/animedl/sites/` con
`get_series()` y `get_sources()`, y añádela a `EXTRACTORS` en `sites/__init__.py`.

Tests: `uv run pytest` (los de descarga y extremo a extremo necesitan `ffmpeg`).

---

Úsalo solo con contenido que tengas derecho a descargar.
