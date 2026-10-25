"""`animedl daemon`: run the update job on a cron schedule (weekly by default)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

log = logging.getLogger(__name__)


def run_daemon(job: Callable[[], None], cron: str, *, run_now: bool = False) -> None:
    scheduler = BlockingScheduler()
    trigger = CronTrigger.from_crontab(cron)

    def safe_job() -> None:
        try:
            job()
        except Exception:
            log.exception("La comprobación programada falló")
        finally:
            nxt = scheduler.get_job("update")
            if nxt and nxt.next_run_time:
                log.info(
                    "Próxima comprobación: %s", nxt.next_run_time.strftime("%Y-%m-%d %H:%M %Z")
                )

    extra = {"next_run_time": datetime.now().astimezone()} if run_now else {}
    scheduler.add_job(
        safe_job,
        trigger,
        id="update",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=6 * 3600,
        **extra,
    )
    next_run = trigger.get_next_fire_time(None, datetime.now(trigger.timezone))
    log.info(
        "Programado «%s». Próxima comprobación: %s",
        cron,
        "ahora" if run_now else next_run.strftime("%Y-%m-%d %H:%M %Z"),
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Daemon detenido")
