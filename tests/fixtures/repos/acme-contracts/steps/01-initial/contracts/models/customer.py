from pydantic import BaseModel


class Customer(BaseModel):
    id: str
    name: str
    country: str = "NL"
    interval: int = 90
