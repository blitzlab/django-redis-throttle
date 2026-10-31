from django.apps import AppConfig


class RedisThrottleConfig(AppConfig):
    name = "redis_throttle"
    verbose_name = "Redis throttle"

    def ready(self) -> None:
        from . import checks  # noqa: F401  (registers system checks)
