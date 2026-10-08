"""Mise en forme des résultats pour le terminal et l'interface."""

from __future__ import annotations

from simulation_offre.model import SimulationResult, Stop


def format_minute(minute: int) -> str:
    hour, rest = divmod(int(minute), 60)
    return f"{hour:02d}:{rest:02d}"


def payload(result: SimulationResult) -> dict:
    return {
        "energie_kwh": round(result.energy_kwh, 1),
        "nb_courses": result.n_courses,
        "attente_moyenne_min": _round(result.mean_wait_min, 2),
        "attente_p90_min": _round(result.p90_wait_min, 2),
        "attente_cumulee_h": round(result.total_wait_min / 60, 1),
        "voyageurs": round(result.served, 1),
        "non_embarques": round(result.unserved, 1),
        "charge_max": round(result.max_load, 1),
        "taux_charge_max": _round(result.max_load_rate, 3),
        "courses_en_surcharge": result.over_capacity,
        "gares_inconnues": list(result.unknown_stations),
        "gares": [
            {
                "rer": station.rer,
                "direction": station.direction,
                "station": station.station,
                "ordre": station.ordre,
                "attente_min": _round(station.mean_wait_min, 2),
                "voyageurs": round(station.passengers, 1),
                "non_embarques": round(station.unserved, 1),
                "charge_max": _round(station.max_load, 1),
                "charge_moyenne": _round(station.mean_load, 1),
                "gare_suivante": station.next_station,
            }
            for station in result.stations
        ],
        "trains": [
            {
                "id": train.course_id,
                "rer": train.rer,
                "direction": train.direction,
                "heure": format_minute(train.origin_minute),
                "minute": train.origin_minute,
                "origine": train.origin_station,
                "charge_max": round(train.max_load, 1),
                "montees": round(train.boardings, 1),
                "ajoutee": train.added,
                "surcharge": train.over_capacity,
                "capacite": train.capacity,
            }
            for train in result.trains
        ],
    }


def ecart(baseline: SimulationResult, scenario: SimulationResult) -> dict:
    return {
        "energie_kwh": round(scenario.energy_kwh - baseline.energy_kwh, 1),
        "attente_moyenne_min": _delta(baseline.mean_wait_min, scenario.mean_wait_min),
        "attente_p90_min": _delta(baseline.p90_wait_min, scenario.p90_wait_min),
        "attente_cumulee_h": round((scenario.total_wait_min - baseline.total_wait_min) / 60, 1),
        "charge_max": round(scenario.max_load - baseline.max_load, 1),
        "non_embarques": round(scenario.unserved - baseline.unserved, 1),
        "nb_courses": scenario.n_courses - baseline.n_courses,
    }


def catalogue(stops: list[Stop]) -> list[dict]:
    grouped: dict[str, list[Stop]] = {}
    for stop in stops:
        grouped.setdefault(stop.course_id, []).append(stop)
    rows = []
    for course_id, group in grouped.items():
        ordered = sorted(group, key=lambda stop: stop.ordre)
        origin = ordered[0]
        rows.append(
            {
                "id": course_id,
                "rer": origin.rer,
                "direction": origin.direction,
                "heure": format_minute(origin.minute),
                "minute": origin.minute,
                "origine": origin.station,
                "terminus": ordered[-1].station,
                "arrets": len(ordered),
            }
        )
    rows.sort(key=lambda row: (row["rer"], row["direction"], row["minute"], row["id"]))
    return rows


def format_report(
    baseline: SimulationResult, scenario: SimulationResult, removed: list[str], added: list[str]
) -> str:
    gap = ecart(baseline, scenario)
    lines = [
        _block("Plan prévu", baseline),
        "",
        _block("Scénario", scenario),
        "",
        "Écart (scénario − plan)",
        f"  Courses            {_signed_int(gap['nb_courses'])}",
        f"  Énergie            {_signed(gap['energie_kwh'], 1)} kWh",
        f"  Attente moyenne    {_signed(gap['attente_moyenne_min'], 2)} min",
        f"  Attente cumulée    {_signed(gap['attente_cumulee_h'], 1)} h",
        f"  Attente p90        {_signed(gap['attente_p90_min'], 2)} min",
        f"  Charge max         {_signed(gap['charge_max'], 1)} voyageurs",
        f"  Non embarqués      {_signed(gap['non_embarques'], 1)}",
    ]
    if removed:
        lines.append("  Courses retirées   " + ", ".join(removed))
    if added:
        lines.append("  Courses ajoutées   " + ", ".join(added))
    rises = _largest_wait_increases(baseline, scenario)
    if rises:
        lines.extend(["", "Gares où l'attente augmente le plus"])
        for label, delta_min, passengers in rises:
            lines.append(
                f"  {label:<32} {_signed(delta_min, 2)} min   ({_fr(passengers, 0)} voyageurs)"
            )
    if scenario.unknown_stations:
        lines.extend(["", "Gares d'affluence absentes du plan :"])
        lines.extend(f"  {name}" for name in scenario.unknown_stations)
    return "\n".join(lines)


def _block(title: str, result: SimulationResult) -> str:
    rate = (
        f"{_fr(100 * result.max_load_rate, 0)} % de la capacité"
        if result.max_load_rate is not None
        else "capacité non renseignée"
    )
    return "\n".join(
        [
            title,
            f"  Courses            {result.n_courses}",
            f"  Énergie            {_fr(result.energy_kwh, 0)} kWh",
            f"  Attente moyenne    {_fr(result.mean_wait_min, 2)} min",
            f"  Attente p90        {_fr(result.p90_wait_min, 2)} min",
            f"  Charge max         {_fr(result.max_load, 0)} voyageurs ({rate})",
            f"  Courses en surcharge {result.over_capacity}",
            f"  Voyageurs embarqués {_fr(result.served, 0)}",
            f"  Non embarqués      {_fr(result.unserved, 0)}",
        ]
    )


def _largest_wait_increases(
    baseline: SimulationResult, scenario: SimulationResult
) -> list[tuple[str, float, float]]:
    base = {
        (station.rer, station.direction, station.station): station for station in baseline.stations
    }
    ranked = []
    for station in scenario.stations:
        previous = base.get((station.rer, station.direction, station.station))
        if previous is None or previous.mean_wait_min is None or station.mean_wait_min is None:
            continue
        delta = station.mean_wait_min - previous.mean_wait_min
        if delta > 0.05 and station.passengers > 0:
            label = f"{station.station} ({station.direction})"
            ranked.append((label, delta, station.passengers))
    ranked.sort(key=lambda item: item[1], reverse=True)
    return ranked[:5]


def _round(value: float | None, digits: int) -> float | None:
    if value is None:
        return None
    return round(value, digits)


def _delta(before: float | None, after: float | None) -> float | None:
    if before is None or after is None:
        return None
    return round(after - before, 2)


def _fr(value: float | None, digits: int) -> str:
    if value is None:
        return "—"
    text = f"{value:,.{digits}f}"
    return text.replace(",", " ").replace(".", ",")


def _signed(value: float | None, digits: int) -> str:
    if value is None:
        return "—"
    sign = "+" if value > 0 else ""
    return sign + _fr(value, digits)


def _signed_int(value: int) -> str:
    sign = "+" if value > 0 else ""
    return sign + str(value)
