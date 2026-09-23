from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.services.jobs import job_backup_sqlite, job_expire_holds, job_reminders


def build_scheduler(bot, session_maker) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
    scheduler.add_job(job_expire_holds, "interval", minutes=1, args=[bot, session_maker], id="expire_holds")
    scheduler.add_job(
        job_reminders,
        "interval",
        minutes=5,
        args=[bot, session_maker],
        id="reminders",
    )
    scheduler.add_job(job_backup_sqlite, "cron", hour=3, minute=15, id="sqlite_backup")
    return scheduler
