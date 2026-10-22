from celery import Celery

celery_app = Celery("acme-worker", broker="redis://localhost:6379/0")
celery_app.autodiscover_tasks(["worker.tasks"])
