from pydantic import BaseModel


class Cadence(BaseModel):
    """How often something repeats."""

    id: str
    interval: int = 1
    unit: str = "day"
