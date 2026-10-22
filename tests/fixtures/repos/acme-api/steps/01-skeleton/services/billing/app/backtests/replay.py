def replay(history: list[dict]) -> list[dict]:
    """Replay a day-by-day rate history for the what-if screen."""
    return [{"day": row["day"], "rate": row["rate"]} for row in history]
