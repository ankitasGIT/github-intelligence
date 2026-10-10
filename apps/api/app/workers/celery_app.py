from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "github_intelligence",
    broker=settings.redis_url,
    backend=settings.redis_url,
)
celery_app.conf.imports = ("app.workers.tasks",)
celery_app.conf.task_track_started = True
