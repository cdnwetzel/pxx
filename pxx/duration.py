from typing import Final

SECONDS_IN_MINUTE: Final = 60
SECONDS_IN_HOUR: Final = 3600


def human_duration(seconds: float) -> str:
    if seconds < SECONDS_IN_MINUTE:
        return f"{int(seconds)}s"
    elif seconds < SECONDS_IN_HOUR:
        minutes = int(seconds // SECONDS_IN_MINUTE)
        remaining_seconds = int(seconds % SECONDS_IN_MINUTE)
        return f"{minutes}m{remaining_seconds:02d}s"
    else:
        hours = int(seconds // SECONDS_IN_HOUR)
        remaining_minutes = int((seconds % SECONDS_IN_HOUR) // SECONDS_IN_MINUTE)
        return f"{hours}h{remaining_minutes:02d}m"
