from acme_contracts.models.schedule import Schedule

from worker import topics
from worker.app import celery_app


@celery_app.task(name=topics.SCHEDULE_TICK)
def days_until_next_run(schedule_json: str) -> int:
    schedule = Schedule.model_validate_json(schedule_json)
    return schedule.interval
