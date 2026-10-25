"""`animedl daemon`: weekly check for new episodes plus a queue of pending downloads."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

log = logging.getLogger(__name__)


def run_daemon(
    job: Callable[[], None],
    cron: str,
    *,
    run_now: bool = False,
    queue_job: Callable[[], None] | None = None,
    queue_interval: int = 60,
) -> None:
    """Run `job` on the cron schedule and `queue_job` every `queue_interval` seconds.

    Both share a lock, so only one of them downloads at a time.
    """
    scheduler = BlockingScheduler()
    trigger = CronTrigger.from_crontab(cron)
    busy = threading.Lock()

    def scheduled() -> None:
        with busy:
            try:
                job()
            except Exception:
                log.exception("La comprobación programada falló")
        nxt = scheduler.get_job("update")
        if nxt and nxt.next_run_time:
            log.info("Próxima comprobación: %s", nxt.next_run_time.strftime("%Y-%m-%d %H:%M %Z"))

    def queued() -> None:
        if queue_job is None or not busy.acquire(blocking=False):
            return  # the weekly check is running and downloads the queue anyway
        try:
            queue_job()
        except Exception:
            log.exception("Fallo al procesar la cola de descargas")
        finally:
            busy.release()

    extra = {"next_run_time": datetime.now().astimezone()} if run_now else {}
    scheduler.add_job(
        scheduled,
        trigger,
        id="update",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=6 * 3600,
        **extra,
    )
    if queue_job is not None:
        scheduler.add_job(
            queued,
            IntervalTrigger(seconds=queue_interval),
            id="queue",
            max_instances=1,
            coalesce=True,
            next_run_time=datetime.now().astimezone(),
        )
    next_run = trigger.get_next_fire_time(None, datetime.now(trigger.timezone))
    log.info(
        "Programado «%s». Próxima comprobación: %s. Cola de descargas: cada %ds",
        cron,
        "ahora" if run_now else next_run.strftime("%Y-%m-%d %H:%M %Z"),
        queue_interval,
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Daemon detenido")
