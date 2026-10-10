"""Explicit metric pause state, including legacy local experiment recordings."""


def metric_is_paused(key, value):
    if not isinstance(value, dict):
        return False
    error = value.get("error")
    # A real error takes priority over a contradictory paused status.
    return (error == f"experiment_{key}_disabled"
            or (value.get("status") == "paused" and not error))
