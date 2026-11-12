"""Transparent business comparison of held-out version scores."""

from decimal import Decimal
from typing import Any


def measure_changes(
    new: dict, old: dict, settings: dict
) -> tuple[list[str], list[str]]:
    better, worse = [], []
    for key in ("accuracy", "grade"):
        difference = Decimal(str(new[key])) - Decimal(str(old[key]))
        if difference > Decimal(str(settings["noise"][key])):
            better.append(key)
        if difference < -Decimal(str(settings["tolerance"][key])):
            worse.append(key)
    for key in ("cost_per_claim", "seconds_per_claim"):
        baseline = Decimal(str(old[key]))
        candidate = Decimal(str(new[key]))
        if candidate < baseline * (1 - Decimal(str(settings["noise"][key]))):
            better.append(key)
        if candidate > baseline * (1 + Decimal(str(settings["tolerance"][key]))):
            worse.append(key)
    return better, worse


def compare_scores(new: dict, old: dict, settings: dict) -> dict[str, Any]:
    if new.get("grade") is None or old.get("grade") is None:
        return {"kept": False, "reason": "A version has no grade"}
    missed = [key for key, floor in settings["objectives"].items() if new[key] < floor]
    if missed:
        return {"kept": False, "reason": f"Missed objectives: {', '.join(missed)}"}
    better, worse = measure_changes(new, old, settings)
    if worse:
        return {"kept": False, "reason": f"Worse beyond tolerance: {', '.join(worse)}"}
    if not better:
        return {"kept": False, "reason": "No measure improved beyond its noise"}
    return {"kept": True, "reason": f"Improved: {', '.join(better)}"}
