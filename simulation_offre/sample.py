"""Journée illustrative sur le RER A, axe Saint-Germain-en-Laye – Marne-la-Vallée–Chessy.

Les volumes et les kWh sont un ordre de grandeur, pas un comptage ni une consommation mesurée.
Les missions sont omnibus sur cet axe, avec un intervalle de 6 minutes en pointe.
"""

from __future__ import annotations

import csv
import math
from pathlib import Path

from simulation_offre.model import Demand, Rate, Stop

KWH_PER_COURSE = 700.0
CAPACITY = 2600.0
# Montées pour un poids de 1, au sommet de la pointe.
SCALE = 1200.0

# Décalage en minutes depuis le terminus ouest de chaque mission.
EAST = "Marne-la-Vallée"
SAINT_GERMAIN = "Saint-Germain-en-Laye"
MLV = (
    ("Saint-Germain-en-Laye", 0),
    ("Le Vésinet-Le Pecq", 3),
    ("Le Vésinet-Centre", 5),
    ("Chatou-Croissy", 8),
    ("Rueil-Malmaison", 11),
    ("Nanterre-Ville", 14),
    ("Nanterre-Université", 17),
    ("Nanterre-Préfecture", 19),
    ("La Défense", 22),
    ("Charles de Gaulle-Étoile", 25),
    ("Auber", 28),
    ("Châtelet-Les Halles", 31),
    ("Gare de Lyon", 34),
    ("Nation", 37),
    ("Val de Fontenay", 44),
    ("Neuilly-Plaisance", 46),
    ("Bry-sur-Marne", 48),
    ("Noisy-le-Grand", 49),
    ("Noisy-Champs", 53),
    ("Noisiel", 56),
    ("Lognes", 58),
    ("Torcy", 61),
    ("Bussy-Saint-Georges", 64),
    ("Val d'Europe", 66),
    ("Marne-la-Vallée-Chessy", 69),
)
AXES = ((EAST, SAINT_GERMAIN, MLV),)
WESTBOUND = {SAINT_GERMAIN}


def build_world() -> tuple[list[Rate], list[Stop], list[Demand]]:
    rates = [Rate(rer="A", kwh_per_course=KWH_PER_COURSE, capacity=CAPACITY)]
    stops: list[Stop] = []
    for east_name, west_name, _stations in AXES:
        for direction in (east_name, west_name):
            layout = _layout(direction)
            for origin_minute in _departures():
                hour, minute = divmod(origin_minute, 60)
                course_id = f"A-{direction}-{hour:02d}{minute:02d}"
                for station, offset in layout:
                    stops.append(
                        Stop(
                            course_id=course_id,
                            rer="A",
                            direction=direction,
                            station=station,
                            ordre=offset,
                            minute=origin_minute + offset,
                        )
                    )

    demand: list[Demand] = []
    for east_name, west_name, _stations in AXES:
        for direction in (east_name, west_name):
            names = [name for name, _offset in _layout(direction)]
            ordered_weights = _boarding_weights(len(names))
            for start in range(6 * 60, 22 * 60, 30):
                coefficient = _time_factor(direction, start + 15)
                for station, weight in zip(names, ordered_weights):
                    boardings = weight * SCALE * coefficient
                    if boardings < 0.5:
                        continue
                    demand.append(
                        Demand(
                            rer="A",
                            direction=direction,
                            station=station,
                            start=start,
                            end=start + 30,
                            boardings=round(boardings, 1),
                        )
                    )
    return rates, stops, demand


def write_csvs(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    rates, stops, demand = build_world()
    _write(
        directory / "consommations.csv",
        ["rer", "kwh_par_course", "capacite"],
        [
            [
                rate.rer,
                _num(rate.kwh_per_course),
                "" if rate.capacity is None else _num(rate.capacity),
            ]
            for rate in rates
        ],
    )
    _write(
        directory / "plan_transport.csv",
        ["course_id", "rer", "direction", "station", "ordre", "heure"],
        [
            [
                stop.course_id,
                stop.rer,
                stop.direction,
                stop.station,
                stop.ordre,
                _clock(stop.minute),
            ]
            for stop in stops
        ],
    )
    _write(
        directory / "affluence.csv",
        ["rer", "direction", "station", "debut_tranche", "fin_tranche", "montees"],
        [
            [
                row.rer,
                row.direction,
                row.station,
                _clock(row.start),
                _clock(row.end),
                _num(row.boardings),
            ]
            for row in demand
        ],
    )


def _layout(direction: str) -> tuple[tuple[str, int], ...]:
    for east_name, west_name, stations in AXES:
        if direction == east_name:
            return stations
        if direction == west_name:
            total = stations[-1][1]
            return tuple((name, total - offset) for name, offset in reversed(stations))
    raise ValueError(f"Sens inconnu : {direction}")


def _boarding_weights(count: int) -> tuple[float, ...]:
    if count < 2:
        return (0.0,) * count
    return tuple(round(1.5 * (count - 1 - index) / (count - 1), 2) for index in range(count))


def _departures() -> list[int]:
    minute = 6 * 60
    end = 22 * 60
    departures = []
    while minute <= end:
        departures.append(minute)
        minute += _step(minute)
    return departures


def _step(minute: int) -> int:
    morning_peak = 7 * 60 <= minute < 9 * 60 + 30
    evening_peak = 16 * 60 + 30 <= minute < 19 * 60
    if morning_peak or evening_peak:
        return 6
    if minute < 7 * 60 or minute >= 21 * 60:
        return 15
    return 12


def _time_factor(direction: str, minute: int) -> float:
    def gauss(center: float, sigma: float) -> float:
        return math.exp(-0.5 * ((minute - center) / sigma) ** 2)

    if direction in WESTBOUND:
        return 0.12 + gauss(8 * 60 + 10, 55) + 0.45 * gauss(18 * 60, 70)
    return 0.12 + 0.40 * gauss(8 * 60 + 20, 70) + gauss(18 * 60 + 10, 55)


def _clock(minute: int) -> str:
    hour, rest = divmod(minute, 60)
    return f"{hour:02d}:{rest:02d}"


def _num(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return str(value)


def _write(path: Path, header: list[str], rows: list[list]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
