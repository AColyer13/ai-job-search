"""Read application state without turning damaged records into an empty queue."""
import json
from pathlib import Path


class StateError(ValueError):
    pass


def load_state(path, *, missing_ok=False):
    path = Path(path)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(state, dict) or any(
            not isinstance(job, dict) or not isinstance(job.get("status"), str)
            for job in state.values()
        ):
            raise ValueError("expected an object of jobs with a status")
        return state
    except FileNotFoundError as exc:
        if missing_ok:
            return {}
        raise StateError(f"Cannot read application state at {path}: file is missing; audit incomplete.") from exc
    except (OSError, ValueError) as exc:
        raise StateError(
            f"Cannot read application state at {path}: {type(exc).__name__}. "
            "Stopped to preserve application history. Restore a verified backup before retrying."
        ) from exc
