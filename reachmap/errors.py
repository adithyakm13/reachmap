"""Errors raised by the core model. Every rule the model enforces raises one of these."""


class ModelError(ValueError):
    """A value breaks a rule of the data model (for example a finding without evidence). `code` is a stable name for the rule when
    a caller needs to tell rules apart (a decision's: `decision_target`, `decision_verdict`, ...); callers never read the message."""

    def __init__(self, message: str = "", code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class WordingError(ModelError):
    """A human-readable field uses wording the product never says (see reachmap.wording)."""


class UnreadableError(ModelError):
    """An unreadable read was used as if it had answered. Carries the reason it could not be read."""

    def __init__(self, message: str, detail: str | None = None) -> None:
        super().__init__(message)
        self.detail = detail


def check_encodable(where: str, text: str) -> str:
    """Refuse a lone surrogate (what `errors="surrogateescape"` leaves behind): it would
    crash later when hashing or writing JSON. Adapters decode with `errors="replace"`."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        raise ModelError(f'{where}: text has a lone surrogate; decode with errors="replace" before building model values') from None
    return text


def expect_keys(name: str, data: object, keys: tuple[str, ...]) -> None:
    """from_dict guard: data must be a dict with exactly these keys (no silent drift)."""
    if not isinstance(data, dict):
        raise ModelError(f"{name}: expected an object, got {type(data).__name__}")
    missing = [k for k in keys if k not in data]
    extra = [k for k in data if k not in keys]
    if missing or extra:
        raise ModelError(f"{name}: missing keys {missing}, unexpected keys {extra}")
