"""Réécriture d'une période avec un intervalle constant entre les courses."""

from __future__ import annotations

from simulation_offre.model import Addition, Stop
from simulation_offre.present import catalogue

PERIODS = {
    "matin": (7 * 60, 10 * 60),
    "soir": (16 * 60, 19 * 60 + 30),
}


def in_period(minute: int, period: str) -> bool:
    bounds = PERIODS.get(period)
    if bounds is None:
        return True
    start, end = bounds
    return start <= minute < end


def mean_headway(minutes: list[int]) -> float:
    unique = sorted(set(minutes))
    if len(unique) < 2:
        return 6.0
    gaps = [right - left for left, right in zip(unique, unique[1:])]
    return sum(gaps) / len(gaps)


def even_times(first: int, last: int, headway: int) -> list[int]:
    if headway < 1:
        raise ValueError("L'intervalle doit être d'au moins une minute")
    if last < first:
        return [first]
    return list(range(first, last + 1, headway))


def headway_changes(
    stops: list[Stop],
    rer: str,
    choices: dict[str, int | None],
) -> tuple[set[str], list[Addition]]:
    """Remplace les départs d'une période par un écart constant.

    ``choices`` associe ``matin``, ``soir`` et ``tout`` à un intervalle en
    minutes. ``None`` conserve l'horaire prévu. ``tout`` remplace la journée
    et ignore les deux pointes.
    """
    rows = [row for row in catalogue(stops) if row["rer"] == rer]
    if choices.get("tout") is not None:
        windows = [("tout", choices["tout"])]
    else:
        windows = [
            (name, choices[name]) for name in ("matin", "soir") if choices.get(name) is not None
        ]

    removed: set[str] = set()
    additions: list[Addition] = []
    directions = sorted({row["direction"] for row in rows})
    for direction in directions:
        for period, headway in windows:
            if headway is None:
                continue
            planned = [
                row
                for row in rows
                if row["direction"] == direction and in_period(row["minute"], period)
            ]
            if len({row["minute"] for row in planned}) < 2:
                continue
            minutes = sorted({row["minute"] for row in planned})
            targets = set(even_times(minutes[0], minutes[-1], headway))
            for row in planned:
                if row["minute"] not in targets:
                    removed.add(row["id"])
            for minute in sorted(targets - set(minutes)):
                additions.append(Addition(rer=rer, direction=direction, minute=minute))
    return removed, additions
