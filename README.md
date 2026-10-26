# animedl

Pegas la URL de una serie y `animedl` busca todos sus episodios, elige un servidor que
funcione y los descarga a tu disco duro con nombres que entienden Plex y Jellyfin. Guarda en
una base de datos SQLite qué tienes y qué no. Una vez por semana revisa las series que siguen
en emisión y descarga los episodios nuevos.

```
/media/series/                               ← biblioteca de tipo «Series» en Plex/Jellyfin
├── Black Clover (2017) {tmdb-73223}/
│   ├── Season 01/Black Clover (2017) - S01E01.mp4 ...
│   └── Season 02/Black Clover (2017) - S02E01.mp4 ...     ← «Black Clover 2nd Season»
└── Attack on Titan (2013) {tmdb-1429}/
    └── Season 03/Attack on Titan (2013) - S03E13.mp4 ...    ← «Season 3 Part 2», ep. 1

/media/movies/                               ← biblioteca de tipo «Películas»
└── A Silent Voice (2016) {tmdb-378064}/A Silent Voice (2016) {tmdb-378064}.mp4
```

## Webs soportadas

| Web | Ejemplo | Cómo se leen los episodios |
| --- | --- | --- |
| AnimeFLV y clones (`animeflv.ba`, `animeflv.net`…) | `https://animeflv.ba/anime/the-promised-neverland-mrwxzt` | animeflv.ba: JSON `af-episodes` + API `ajax/server/list`; animeflv.net: variables `episodes`/`videos` |
| JKAnime | `https://jkanime.net/black-clover-2nd-season/` | API AJAX de episodios (la nueva y la antigua); si no, enlaces de la página |
| AnimeOnline.ninja | `https://ww3.animeonline.ninja/online/kanojo-no-tomodachi/` | tema DooPlay de WordPress (temporadas y API del reproductor) |
| SoloLatino | `https://sololatino.co/serie/yosuga-no-sora` (o la URL de un episodio) | episodios, estado e IMDb sí; **los vídeos no**: su reproductor (embed69) cifra los enlaces tras un proof-of-work antibots que animedl no resuelve |
| Cualquier otra | — | extractor genérico (enlaces de episodios + iframes); puede o no funcionar |

**Servidores de vídeo:** ok.ru, Streamwish, VidHide/Filelions, Filemoon, Mp4upload,
YourUpload, los reproductores propios de JKAnime, Streamtape, Voe, Doodstream, Mixdrop, MEGA y
1Fichier. 1Fichier usa la descarga gratuita: un fichero a la vez y, si pide esperar entre
descargas, el episodio vuelve a la cola sin contar como fallo. Por cada episodio se prueban por
orden de preferencia (idioma y servidor) hasta que uno funciona; si un servidor dice que el
vídeo está borrado, se pasa al siguiente.

> **Estado (octubre 2026), probado desde un servidor en la nube:**
> - **animeflv.ba:** verificado contra la web real. Lista completa de episodios (también series
>   largas como One Piece, con 1180), estado de emisión, y servidores MP4Upload, Voe, MEGA,
>   UPNShare y 1Fichier. MP4Upload se resuelve a un `.mp4` directo. La descarga de los bytes no
>   se pudo probar allí: el CDN de MP4Upload usa el puerto 183 y MEGA usa HTTP, y el proxy de ese
>   servidor solo dejaba salir por HTTPS en el 443. En un ordenador normal no hay esa restricción.
> - **jkanime.net** y **animeonline.ninja:** desde la IP del servidor en la nube Cloudflare pedía
>   el desafío interactivo, así que sus extractores siguen sin probarse contra la web real. Desde
>   una IP de casa puede que no salte; si salta, usa `cookies_file` (ver
>   [Diagnóstico](#diagnóstico)).
> - **Voe** estaba detrás de DDoS-Guard desde esa IP. **UPNShare** cifra el vídeo en su
>   reproductor y de momento se ignora.
> - La descarga (yt-dlp, HLS, base de datos, programación) está probada de principio a fin
>   contra una web falsa en local.

## Instalación

### Opción A: Docker (recomendada para dejarlo funcionando solo)

Solo necesitas [Docker](https://docs.docker.com/get-docker/) (Docker Desktop en Windows/Mac).
La imagen ya trae Python, ffmpeg y megatools.

```bash
git clone -b claude/elegant-bohr-w734ct https://github.com/blascokoa/animeDownload.git
cd animeDownload
cp .env.example .env        # edita SERIES_DIR, MOVIES_DIR y PUID/PGID (salida de `id`)
docker compose up -d --build
```

`SERIES_DIR` (por ejemplo `/media/series`) y `MOVIES_DIR` (por ejemplo `/media/movies`) son dos
carpetas separadas, y no conviene que una esté dentro de la otra: cada una será una biblioteca
distinta en Plex/Jellyfin.

El contenedor se queda en marcha. Cada minuto descarga lo que haya pendiente, y cada lunes a
las 04:00 (`ANIMEDL_SCHEDULE` en `.env`) busca episodios nuevos de las series en emisión. La
base de datos queda en `./data` y los vídeos en `SERIES_DIR` y `MOVIES_DIR`. Para darle órdenes usa
`./animedl.sh`: si el daemon está en marcha ejecuta el comando dentro de él, y si no, en un
contenedor temporal.

```bash
./animedl.sh add -b https://animeflv.ba/anime/a-silent-voice-il9eem   # en segundo plano
docker compose logs -f                                                # ver el progreso
./animedl.sh add https://animeflv.ba/anime/the-promised-neverland-mrwxzt  # en primer plano
./animedl.sh list
./animedl.sh probe https://animeflv.ba/anime/the-promised-neverland-mrwxzt -e 1 --resolve
docker compose up -d --build   # tras actualizar el código (git pull)
```

En Windows sin WSL, usa `docker compose run --rm animedl add URL` en lugar de `./animedl.sh`, y
pon rutas como `D:/Anime/Series` y `D:/Anime/Peliculas`.

**Si descargaste con una versión anterior**, que lo guardaba todo bajo `ANIME_DIR`, deja `ANIME_DIR`
en `.env` y ejecuta una vez `./animedl.sh rename`. Mueve cada fichero a `SERIES_DIR` o
`MOVIES_DIR` y borra las carpetas antiguas que se queden vacías. Después puedes quitar
`ANIME_DIR` del `.env`.

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

# En segundo plano: solo la registra y la descarga el daemon (cada minuto mira la cola)
animedl add -b URL1 URL2

# Ya tengo hasta el 100: registrar y descargar solo del 101 en adelante
animedl add https://jkanime.net/black-clover/ --from-episode 101

# Forzar nombre de carpeta y temporada (normalmente se deducen solos, ver más abajo)
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

### Primer plano, segundo plano y Ctrl+C

- Se descargan **3 episodios a la vez** (`parallel_downloads`; en Docker
  `ANIMEDL_PARALLEL_DOWNLOADS` en `.env`), de la misma serie o de varias. Muchos servidores
  limitan la velocidad de cada conexión, así que en paralelo se tarda mucho menos.
- `add URL` descarga en primer plano, con una barra de progreso por episodio.
- `add -b URL` (o `--background`) solo registra la serie. El daemon (`animedl daemon` o el
  contenedor de Docker) revisa la cola cada minuto (`queue_interval`) y la descarga. El progreso
  se ve en sus logs.
- **Ctrl+C** durante una descarga en primer plano: los episodios en curso vuelven a «pendiente»
  sin contar como intento fallido. Lo que se había bajado se descarta y la próxima vez empieza de cero (lo
  hará el daemon si está en marcha). Si no quieres ese episodio:
  `animedl mark SERIE --as skipped -e N`.
- Si el contenedor se para a mitad de una descarga, ese episodio vuelve a la cola a los 30
  minutos.

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

## Nombres para Plex y Jellyfin

Los nombres siguen las guías oficiales de
[Plex](https://support.plex.tv/articles/naming-and-organizing-your-tv-show-files/) y
[Jellyfin](https://jellyfin.org/docs/general/server/media/shows):
`Título (Año) {tmdb-ID}/Season NN/Título (Año) - SNNEMM`. El `{tmdb-ID}` en la carpeta hace que los
dos reconozcan la serie sin depender del título; Jellyfin acepta esa misma forma con llaves.

Al añadir una serie, `animedl` la identifica en [AniList](https://anilist.co) (animeflv.ba enlaza
su ficha; en las demás webs se busca por título). Después usa las
[listas de IDs de Fribb](https://github.com/Fribb/anime-lists) para saber su ID de TMDB, en qué
temporada de TMDB cae y desde qué episodio. TMDB es el orden por defecto de Plex y el proveedor
por defecto de Jellyfin. Por eso:

- Las secuelas van dentro de la serie original con su número de temporada, y las temporadas
  partidas en dos llevan el número de episodio que les da TMDB (Attack on Titan S3 Part 2
  empieza en el S03E13).
- Las películas van a la carpeta de películas (`MOVIES_DIR`) con el formato de película.
- Los OVA que TMDB considera especiales y los episodios «12.5» van a `Season 00`.
- Las series largas que TMDB no divide en temporadas, como One Piece, usan `{tvdb-ID}` y
  numeración absoluta. **En Plex** elige para esa serie *Episode ordering → TheTVDB (Absolute)*
  (Editar → Avanzado). **En Jellyfin** instala el plugin de TheTVDB y elige el orden «Absolute».

En Plex/Jellyfin crea **dos bibliotecas**: una de tipo *Series* (*Shows*/*TV Shows*) con la
carpeta de series y otra de tipo *Películas* con la de películas. Si una biblioteca de películas
incluye la carpeta de series, Jellyfin muestra cada episodio como una película suelta.

Comandos:

```bash
animedl list                    # debajo de cada serie, cómo la verán Plex/Jellyfin
animedl rename --dry-run        # qué ficheros ya descargados se moverían al formato nuevo
animedl rename                  # moverlos (identifica antes las series que no lo estén)
animedl identify 3 --anilist 101759    # corregir una identificación (ID de anilist.co/anime/ID)
animedl edit 3 --season 2 --id tmdb-83097 --year 2019   # o a mano; mueve los ficheros
```

Si AniList no encuentra la serie, o no está en las listas de IDs, el nombre sale de la web (por
ejemplo, «X 2nd Season» → `X/Season 02`) sin ID. Plex/Jellyfin la buscarán entonces por el título.
Con la identificación desactivada (`identify = false`) se usa siempre el título de la web.

## Configuración

Copia `config.example.toml` a `config.toml`, o a `~/.config/animedl/config.toml`; con Docker,
a `data/config.toml`. Cada opción se puede dar también como variable de entorno
`ANIMEDL_<OPCIÓN>`. Las más útiles:

| Opción | Por defecto | Para qué |
| --- | --- | --- |
| `series_dir`, `movies_dir` | `download_dir/Series`, `download_dir/Peliculas` | carpetas de series y de películas (en Docker: `SERIES_DIR` y `MOVIES_DIR` en `.env`) |
| `download_dir` | `~/Anime` | carpeta base si no defines las dos anteriores |
| `database` | `~/.local/share/animedl/animedl.db` | base de datos SQLite |
| `languages` | `["sub", "lat", "cast"]` | versiones aceptadas, en orden de preferencia |
| `servers` | okru, streamwish, vidhide, … | orden en que se prueban los servidores |
| `schedule` | `0 4 * * 1` | cron de `animedl daemon` |
| `parallel_downloads` | `3` | episodios que se descargan a la vez |
| `filename_template`, `movie_template` | ver [Nombres para Plex y Jellyfin](#nombres-para-plex-y-jellyfin) | nombre de los ficheros |
| `identify`, `title_language` | `true`, `english` | identificar en AniList/TMDB; títulos en inglés o romaji |
| `cookies_file`, `user_agent` | — | para webs con Cloudflare (ver abajo) |

## Diagnóstico

`probe` muestra lo que se extrae de una URL sin tocar la base de datos:

```bash
animedl probe https://jkanime.net/black-clover-2nd-season/                 # título, estado, episodios
animedl probe https://jkanime.net/black-clover-2nd-season/ -e 1            # servidores del episodio 1
animedl probe https://jkanime.net/black-clover-2nd-season/ -e 1 --resolve  # y los enlaces de vídeo
animedl -v probe URL -e 1 --resolve --dump debug/                          # guarda el HTML en debug/
animedl dump URL_DE_UN_EPISODIO --out debug/   # guarda la página y sus reproductores (web nueva)
```

Problemas típicos:

- **`docker compose build` falla con «Temporary failure resolving 'deb.debian.org'»:** pasa en
  Linux con systemd-resolved (DNS en `127.0.0.53`), que no es accesible desde los
  contenedores. `docker-compose.yml` ya construye la imagen con la red del equipo
  (`build.network: host`) y da a los contenedores los DNS públicos 1.1.1.1 y 8.8.8.8. Si en tu
  red tienes que usar otro DNS (Pi-hole, el de la empresa...), cámbialo en `dns:`.
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
├── metadata.py     identificación en AniList + IDs de TMDB/TVDB (temporada, desfase, película)
├── naming.py       nombres de ficheros para Plex/Jellyfin
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
