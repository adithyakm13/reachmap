import logging
from collections.abc import Callable

from acme_common.money import cents_to_decimal

log = logging.getLogger(__name__)
REGISTRY: dict[str, Callable[[], None]] = {}


def scheduled(*, name: str, cron: str, retries: int = 0):
    def register(func: Callable[[], None]) -> Callable[[], None]:
        REGISTRY[name] = func
        return func

    return register


def _log(message: str) -> None:
    log.info("%s", message)


@scheduled(
    name="nightly-settlement",
    cron="0 2 * * *",
    retries=3,
)
def run_nightly_settlement() -> None:
    _log("starting nightly settlement")
    opening = cents_to_decimal(0)
    _log(f"opening balance {opening}")


@scheduled(name="weekly-statement", cron="0 6 * * 1")
def run_weekly_statement() -> None:
    _log("building weekly statements")
