"""Lecture des trois CSV d'entrée."""

from __future__ import annotations

import csv
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from simulation_offre.model import Demand, Rate, Stop, _normalize_times

_CLOCK = re.compile(r"^(\d{1,2}):(\d{2})(?::\d{2})?$")
_DATED = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[ T](\d{1,2}):(\d{2})(?::(\d{2}))?$")

_ALIASES = {
    "ligne": "rer",
    "line": "rer",
    "sens": "direction",
    "gare": "station",
    "arret": "station",
    "ordre_arret": "ordre",
    "sequence": "ordre",
    "heure_depart": "heure",
    "depart": "heure",
    "course": "course_id",
    "id_course": "course_id",
    "train": "course_id",
    "debut": "debut_tranche",
    "tranche": "debut_tranche",
    "fin": "fin_tranche",
    "montees_prevues": "montees",
    "affluence": "montees",
    "voyageurs": "montees",
    "kwh": "kwh_par_course",
    "consommation_kwh": "kwh_par_course",
    "consommation": "kwh_par_course",
    "capacity": "capacite",
}


def load_inputs(
    consommations: Path, plan: Path, affluence: Path
) -> tuple[list[Rate], list[Stop], list[Demand]]:
    rates = load_rates(parse_csv(consommations.read_text(encoding="utf-8-sig"), str(consommations)))
    stops = load_stops(parse_csv(plan.read_text(encoding="utf-8-sig"), str(plan)))
    demand = load_demand(parse_csv(affluence.read_text(encoding="utf-8-sig"), str(affluence)))
    return rates, stops, demand


def parse_csv(text: str, source: str = "csv") -> list[dict[str, str]]:
    sample = text.lstrip("\ufeff")
    if not sample.strip():
        raise ValueError(f"{source} est vide")
    first = sample.splitlines()[0]
    delimiter = ";" if first.count(";") > first.count(",") else ","
    reader = csv.DictReader(sample.splitlines(), delimiter=delimiter)
    if not reader.fieldnames:
        raise ValueError(f"{source} n'a pas d'en-tête")
    rows: list[dict[str, str]] = []
    for raw in reader:
        row: dict[str, str] = {}
        for key, value in raw.items():
            if key is None:
                continue
            row[_header(key)] = (value or "").strip()
        if any(row.values()):
            rows.append(row)
    if not rows:
        raise ValueError(f"{source} ne contient aucune ligne")
    return rows


def load_rates(rows: list[dict[str, str]]) -> list[Rate]:
    rates: list[Rate] = []
    for index, row in enumerate(rows, start=2):
        rer = _require(row, "rer", index)
        kwh = _number(_require(row, "kwh_par_course", index), f"ligne {index}, kwh_par_course")
        capacity = None
        if row.get("capacite"):
            capacity = _number(row["capacite"], f"ligne {index}, capacite")
        direction = row.get("direction") or None
        rates.append(Rate(rer=rer, kwh_per_course=kwh, capacity=capacity, direction=direction))
    return rates


def load_stops(rows: list[dict[str, str]]) -> list[Stop]:
    stops: list[Stop] = []
    for index, row in enumerate(rows, start=2):
        stops.append(
            Stop(
                course_id=_require(row, "course_id", index),
                rer=_require(row, "rer", index),
                direction=_require(row, "direction", index),
                station=_require(row, "station", index),
                ordre=int(_number(_require(row, "ordre", index), f"ligne {index}, ordre")),
                minute=_clock(_require(row, "heure", index), f"ligne {index}"),
            )
        )
    return _normalize_times(stops)


def load_demand(rows: list[dict[str, str]]) -> list[Demand]:
    pending = []
    for index, row in enumerate(rows, start=2):
        start, end, day = _window(
            _require(row, "debut_tranche", index),
            row.get("fin_tranche") or "",
            index,
        )
        pending.append(
            {
                "rer": _require(row, "rer", index),
                "direction": _require(row, "direction", index),
                "station": _require(row, "station", index),
                "start": start,
                "end": end,
                "day": day,
                "boardings": _number(_require(row, "montees", index), f"ligne {index}, montees"),
            }
        )
    _fill_bucket_ends(pending)
    return [
        Demand(
            rer=item["rer"],
            direction=item["direction"],
            station=item["station"],
            start=item["start"],
            end=item["end"],
            boardings=item["boardings"],
            day=item["day"],
        )
        for item in pending
    ]


def _fill_bucket_ends(rows: list[dict]) -> None:
    grouped: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for row in rows:
        grouped[(row["rer"], row["direction"], row["station"])].append(row)
    for group in grouped.values():
        group.sort(key=lambda item: item["start"])
        gaps = [
            nxt["start"] - current["start"]
            for current, nxt in zip(group, group[1:])
            if nxt["start"] > current["start"]
        ]
        default_gap = int(round(sum(gaps) / len(gaps))) if gaps else 30
        for index, item in enumerate(group):
            if item["end"] is not None:
                continue
            if index + 1 < len(group) and group[index + 1]["start"] > item["start"]:
                item["end"] = group[index + 1]["start"]
            else:
                item["end"] = item["start"] + default_gap


def _header(value: str) -> str:
    text = value.strip().lower().replace(" ", "_")
    for source, target in (("é", "e"), ("è", "e"), ("ê", "e"), ("à", "a"), ("ô", "o"), ("û", "u")):
        text = text.replace(source, target)
    return _ALIASES.get(text, text)


def _require(row: dict[str, str], key: str, line: int) -> str:
    value = row.get(key, "")
    if not value:
        raise ValueError(f"Ligne {line} : colonne « {key} » manquante ou vide")
    return value


def _number(value: str, label: str) -> float:
    text = value.replace("\u00a0", "").replace(" ", "")
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    else:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError as exc:
        raise ValueError(f"Nombre invalide ({label}) : {value}") from exc


def _window(start: str, end: str, line: int) -> tuple[int, int | None, str | None]:
    label = f"ligne {line}"
    start_at = _dated(start, label)
    if start_at is None:
        clock_end = _clock(end, label) if end else None
        return _clock(start, label), clock_end, None
    if not end:
        raise ValueError(f"Ligne {line} : colonne « fin_tranche » manquante ou vide")
    end_at = _dated(end, label)
    if end_at is None:
        raise ValueError(f"Horaire invalide ({label}) : {end}. Format attendu YYYY-MM-DD HH:MM")
    if end_at <= start_at:
        raise ValueError(f"Tranche invalide ({label}) : {start} → {end}")
    origin = start_at.replace(hour=0, minute=0, second=0, microsecond=0)
    return _elapsed(start_at, origin), _elapsed(end_at, origin), start_at.date().isoformat()


def _dated(value: str, label: str) -> datetime | None:
    match = _DATED.match(value.strip())
    if not match:
        return None
    year, month, day, hour, minute, second = match.groups()
    try:
        return datetime(int(year), int(month), int(day), int(hour), int(minute), int(second or 0))
    except ValueError as exc:
        raise ValueError(f"Horaire invalide ({label}) : {value}") from exc


def _elapsed(moment: datetime, origin: datetime) -> int:
    return int((moment - origin).total_seconds() // 60)


def _clock(value: str, label: str) -> int:
    match = _CLOCK.match(value.strip())
    if not match:
        raise ValueError(
            f"Horaire invalide ({label}) : {value}. Format attendu HH:MM ou YYYY-MM-DD HH:MM"
        )
    hour = int(match.group(1))
    minute = int(match.group(2))
    if minute > 59:
        raise ValueError(f"Horaire invalide ({label}) : {value}")
    return hour * 60 + minute
