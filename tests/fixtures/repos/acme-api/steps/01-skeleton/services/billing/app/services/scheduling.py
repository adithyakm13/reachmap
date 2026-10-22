from datetime import date, timedelta

from acme_contracts.models.schedule import Schedule


def next_run(schedule: Schedule, last_run: date) -> date:
    return last_run + timedelta(days=schedule.interval)
