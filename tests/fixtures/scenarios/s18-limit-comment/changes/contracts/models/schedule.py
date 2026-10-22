from pydantic import BaseModel


class Schedule(BaseModel):
    id: str
    name: str
    interval: int = 1
    limit: int = 250
