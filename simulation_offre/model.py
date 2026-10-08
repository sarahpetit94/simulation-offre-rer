"""Simulation d'une journée d'offre RER.

Entrées :
- une consommation par course, pour chaque RER (et éventuellement chaque sens) ;
- un plan de transport : les arrêts horaires de chaque course ;
- une prévision de montées par gare, sens et tranche horaire.

Les voyageurs d'une tranche arrivent uniformément et prennent le prochain train
qui dessert leur gare. La charge à bord est reconstruite en envoyant chaque
montée vers l'aval, au prorata de l'attractivité des gares suivantes.
L'attractivité d'une gare est le volume de montées du sens inverse sur la
journée (les gens qui en partent dans l'autre sens). Sans sens inverse, la
répartition vers l'aval est uniforme.

La consommation est celle d'une course réalisée : elle ne dépend pas de la
charge. Retirer une course enlève exactement son kWh ; en ajouter une l'ajoute.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Rate:
    rer: str
    kwh_per_course: float
    capacity: float | None = None
    direction: str | None = None


@dataclass(frozen=True)
class Stop:
    course_id: str
    rer: str
    direction: str
    station: str
    ordre: int
    minute: int


@dataclass(frozen=True)
class Demand:
    rer: str
    direction: str
    station: str
    start: int
    end: int
    boardings: float
    day: str | None = None


@dataclass(frozen=True)
class Addition:
    rer: str
    direction: str
    minute: int
    template_course_id: str | None = None


@dataclass(frozen=True)
class StationStat:
    rer: str
    direction: str
    station: str
    ordre: int
    mean_wait_min: float | None
    passengers: float
    unserved: float
    max_load: float | None
    mean_load: float | None
    next_station: str | None


@dataclass(frozen=True)
class TrainStat:
    course_id: str
    rer: str
    direction: str
    origin_minute: int
    origin_station: str
    max_load: float
    boardings: float
    added: bool
    over_capacity: bool
    capacity: float | None
    segment_loads: tuple[float, ...] = ()


@dataclass(frozen=True)
class SimulationResult:
    energy_kwh: float
    n_courses: int
    mean_wait_min: float | None
    p90_wait_min: float | None
    total_wait_min: float
    served: float
    unserved: float
    boarded: float
    alighted: float
    terminus_boardings: float
    max_load: float
    max_load_rate: float | None
    over_capacity: int
    unknown_stations: tuple[str, ...]
    stations: tuple[StationStat, ...]
    trains: tuple[TrainStat, ...]


def apply_changes(
    stops: list[Stop],
    removed: set[str] | frozenset[str],
    additions: list[Addition],
) -> tuple[list[Stop], list[str]]:
    """Retire des courses du plan et ajoute des courses calquées sur une marche existante."""
    stops = _normalize_times(stops)
    known = {stop.course_id for stop in stops}
    missing = sorted(set(removed) - known)
    if missing:
        raise ValueError("Courses inconnues dans le plan : " + ", ".join(missing))

    kept = [stop for stop in stops if stop.course_id not in removed]
    added_ids: list[str] = []
    taken = set(known)
    for addition in additions:
        new_stops, new_id = _build_addition(stops, addition, taken)
        kept.extend(new_stops)
        added_ids.append(new_id)
        taken.add(new_id)
    return kept, added_ids


def simulate(
    rates: list[Rate],
    stops: list[Stop],
    demand: list[Demand],
    added_ids: Collection[str] = (),
) -> SimulationResult:
    stops = _normalize_times(stops)
    _validate_stops(stops)
    specific, generic = _index_rates(rates)

    by_key: dict[tuple[str, str], list[Stop]] = defaultdict(list)
    for stop in stops:
        by_key[(stop.rer, stop.direction)].append(stop)

    demand_by_key: dict[tuple[str, str], list[Demand]] = defaultdict(list)
    for row in demand:
        if row.end <= row.start:
            raise ValueError(
                f"Tranche invalide à {row.station} ({row.direction}) : {row.start} → {row.end}"
            )
        if row.boardings < 0:
            raise ValueError(f"Montées négatives à {row.station} ({row.direction})")
        demand_by_key[(row.rer, row.direction)].append(row)

    opposite_volume = _opposite_boardings(demand)

    station_stats: list[StationStat] = []
    train_stats: list[TrainStat] = []
    pieces: list[tuple[float, float, float]] = []
    unknown: set[str] = set()
    energy = 0.0
    total_wait = 0.0
    served = 0.0
    unserved = 0.0
    boarded = 0.0
    alighted = 0.0
    terminus_boardings = 0.0
    max_load = 0.0
    max_rate: float | None = None
    over_capacity = 0
    seen_courses: set[str] = set()

    keys = set(by_key) | set(demand_by_key)
    for rer, direction in sorted(keys):
        group = by_key.get((rer, direction), [])
        rows = demand_by_key.get((rer, direction), [])
        layout = _station_layout(group) if group else []
        known_stations = {station for _, station in layout}

        for row in rows:
            if row.station not in known_stations:
                unknown.add(f"{rer} / {direction} / {row.station}")
                unserved += row.boardings

        if not group:
            continue

        rate = _lookup_rate(specific, generic, rer, direction)
        for stop in group:
            if stop.course_id not in seen_courses:
                seen_courses.add(stop.course_id)
                energy += rate.kwh_per_course

        departures = _departures_by_station(group)
        attractiveness = _attractiveness(
            [station for _, station in layout],
            opposite_volume.get((rer, direction), {}),
        )
        probs = _destination_probabilities(attractiveness)

        board: dict[str, list[float]] = {
            course_id: [0.0] * len(layout) for course_id in {s.course_id for s in group}
        }
        station_wait = [0.0] * len(layout)
        station_served = [0.0] * len(layout)
        station_unserved = [0.0] * len(layout)
        index_of = {station: index for index, (_, station) in enumerate(layout)}

        for row in rows:
            index = index_of.get(row.station)
            if index is None:
                continue
            assigned, wait, missed, row_pieces = _assign_boardings(
                departures[row.station], row.start, row.end, row.boardings
            )
            pieces.extend(row_pieces)
            total_wait += wait
            served += row.boardings - missed
            unserved += missed
            station_wait[index] += wait
            station_served[index] += row.boardings - missed
            station_unserved[index] += missed
            if index == len(layout) - 1:
                terminus_boardings += row.boardings - missed
                continue
            for course_id, count in assigned.items():
                board[course_id][index] += count

        alight = {course_id: [0.0] * len(layout) for course_id in board}
        for course_id, counts in board.items():
            for origin, count in enumerate(counts):
                if count == 0:
                    continue
                boarded += count
                for dest in range(origin + 1, len(layout)):
                    share = count * probs[origin][dest]
                    alight[course_id][dest] += share
                    alighted += share

        loads: dict[str, list[float]] = {}
        for course_id in board:
            running = 0.0
            segment_loads: list[float] = []
            for index in range(len(layout)):
                running += board[course_id][index] - alight[course_id][index]
                if running < 0 and running > -1e-6:
                    running = 0.0
                if index < len(layout) - 1:
                    segment_loads.append(running)
            loads[course_id] = segment_loads

        origin_of = _origin_by_course(group)
        for course_id in sorted(board, key=lambda cid: (origin_of[cid][0], cid)):
            segment_loads = loads[course_id]
            peak = max(segment_loads) if segment_loads else 0.0
            max_load = max(max_load, peak)
            rate_value = None
            exceeds = False
            if rate.capacity and rate.capacity > 0:
                rate_value = peak / rate.capacity
                exceeds = peak > rate.capacity + 1e-6
                if max_rate is None or rate_value > max_rate:
                    max_rate = rate_value
            if exceeds:
                over_capacity += 1
            minute, origin_station = origin_of[course_id]
            train_stats.append(
                TrainStat(
                    course_id=course_id,
                    rer=rer,
                    direction=direction,
                    origin_minute=minute,
                    origin_station=origin_station,
                    max_load=peak,
                    boardings=sum(board[course_id]),
                    added=course_id in added_ids,
                    over_capacity=exceeds,
                    capacity=rate.capacity,
                    segment_loads=tuple(segment_loads),
                )
            )

        for index, (ordre, station) in enumerate(layout):
            passengers = station_served[index]
            mean_wait = station_wait[index] / passengers if passengers else None
            if index < len(layout) - 1:
                segment = [loads[cid][index] for cid in loads]
                peak_segment = max(segment) if segment else 0.0
                mean_segment = sum(segment) / len(segment) if segment else 0.0
                nxt = layout[index + 1][1]
            else:
                peak_segment = None
                mean_segment = None
                nxt = None
            station_stats.append(
                StationStat(
                    rer=rer,
                    direction=direction,
                    station=station,
                    ordre=ordre,
                    mean_wait_min=mean_wait,
                    passengers=passengers,
                    unserved=station_unserved[index],
                    max_load=peak_segment,
                    mean_load=mean_segment,
                    next_station=nxt,
                )
            )

    mean_wait = total_wait / served if served else None
    return SimulationResult(
        energy_kwh=energy,
        n_courses=len(seen_courses),
        mean_wait_min=mean_wait,
        p90_wait_min=_mixture_quantile(pieces, 0.9),
        total_wait_min=total_wait,
        served=served,
        unserved=unserved,
        boarded=boarded,
        alighted=alighted,
        terminus_boardings=terminus_boardings,
        max_load=max_load,
        max_load_rate=max_rate,
        over_capacity=over_capacity,
        unknown_stations=tuple(sorted(unknown)),
        stations=tuple(station_stats),
        trains=tuple(train_stats),
    )


def _build_addition(
    stops: list[Stop], addition: Addition, taken: set[str]
) -> tuple[list[Stop], str]:
    candidates = [
        stop for stop in stops if stop.rer == addition.rer and stop.direction == addition.direction
    ]
    if not candidates:
        raise ValueError(f"Aucune course de modèle pour {addition.rer} sens {addition.direction}")
    by_course: dict[str, list[Stop]] = defaultdict(list)
    for stop in candidates:
        by_course[stop.course_id].append(stop)

    if addition.template_course_id:
        if addition.template_course_id not in by_course:
            raise ValueError(f"Course modèle inconnue pour ce sens : {addition.template_course_id}")
        template_id = addition.template_course_id
    else:
        template_id = min(
            by_course,
            key=lambda cid: (
                -len(by_course[cid]),
                min(stop.ordre for stop in by_course[cid]),
                cid,
            ),
        )

    template = sorted(by_course[template_id], key=lambda stop: stop.ordre)
    origin_minute = template[0].minute
    new_id = _addition_id(addition, taken)
    created = [
        replace(stop, course_id=new_id, minute=stop.minute - origin_minute + addition.minute)
        for stop in template
    ]
    return created, new_id


def _addition_id(addition: Addition, taken: set[str]) -> str:
    hour, minute = divmod(addition.minute, 60)
    base = f"ajout-{addition.rer}-{addition.direction}-{hour:02d}{minute:02d}"
    if base not in taken:
        return base
    suffix = 2
    while f"{base}-{suffix}" in taken:
        suffix += 1
    return f"{base}-{suffix}"


def _normalize_times(stops: list[Stop]) -> list[Stop]:
    grouped: dict[str, list[Stop]] = defaultdict(list)
    for stop in stops:
        grouped[stop.course_id].append(stop)
    normalized: list[Stop] = []
    for course_id, group in grouped.items():
        ordered = sorted(group, key=lambda stop: (stop.ordre, stop.station))
        previous = None
        for stop in ordered:
            minute = stop.minute
            if previous is not None and minute < previous:
                minute += 24 * 60
            if previous is not None and minute < previous:
                raise ValueError(
                    f"Horaires non croissants sur la course {course_id} à {stop.station}"
                )
            previous = minute
            normalized.append(stop if minute == stop.minute else replace(stop, minute=minute))
    return normalized


def _validate_stops(stops: list[Stop]) -> None:
    seen: dict[str, tuple[str, str]] = {}
    stations: dict[tuple[str, str, str], int] = {}
    duplicates: set[tuple[str, str]] = set()
    for stop in stops:
        identity = (stop.rer, stop.direction)
        previous = seen.setdefault(stop.course_id, identity)
        if previous != identity:
            raise ValueError(f"La course {stop.course_id} mélange plusieurs RER ou plusieurs sens")
        key = (stop.rer, stop.direction, stop.station)
        ordre = stations.get(key)
        if ordre is None:
            stations[key] = stop.ordre
        elif ordre != stop.ordre:
            raise ValueError(
                f"La gare {stop.station} n'a pas le même ordre sur toutes les courses "
                f"({stop.rer}, {stop.direction})"
            )
        mark = (stop.course_id, stop.station)
        if mark in duplicates:
            raise ValueError(f"Arrêt en double : course {stop.course_id}, gare {stop.station}")
        duplicates.add(mark)


def _index_rates(
    rates: list[Rate],
) -> tuple[dict[tuple[str, str], Rate], dict[str, Rate]]:
    specific: dict[tuple[str, str], Rate] = {}
    generic: dict[str, Rate] = {}
    if not rates:
        raise ValueError("Aucune consommation énergétique")
    for rate in rates:
        if rate.kwh_per_course < 0:
            raise ValueError(f"Consommation négative pour le RER {rate.rer}")
        if rate.direction:
            specific[(rate.rer, rate.direction)] = rate
        else:
            generic[rate.rer] = rate
    return specific, generic


def _lookup_rate(
    specific: dict[tuple[str, str], Rate],
    generic: dict[str, Rate],
    rer: str,
    direction: str,
) -> Rate:
    if (rer, direction) in specific:
        return specific[(rer, direction)]
    if rer in generic:
        return generic[rer]
    raise ValueError(f"Pas de consommation pour le RER {rer} (sens {direction})")


def _station_layout(stops: list[Stop]) -> list[tuple[int, str]]:
    ordre_of: dict[str, int] = {}
    for stop in stops:
        ordre_of[stop.station] = stop.ordre
    return sorted((ordre, station) for station, ordre in ordre_of.items())


def _departures_by_station(stops: list[Stop]) -> dict[str, list[tuple[str, int]]]:
    grouped: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for stop in stops:
        grouped[stop.station].append((stop.course_id, stop.minute))
    for station, departures in grouped.items():
        departures.sort(key=lambda item: (item[1], item[0]))
    return grouped


def _origin_by_course(stops: list[Stop]) -> dict[str, tuple[int, str]]:
    best: dict[str, Stop] = {}
    for stop in stops:
        current = best.get(stop.course_id)
        if current is None or stop.ordre < current.ordre:
            best[stop.course_id] = stop
    return {course_id: (stop.minute, stop.station) for course_id, stop in best.items()}


def _opposite_boardings(
    demand: list[Demand],
) -> dict[tuple[str, str], dict[str, float]]:
    """Pour chaque sens, montées des autres sens du même RER, par gare."""
    volume: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for row in demand:
        volume[(row.rer, row.direction)][row.station] += row.boardings

    directions_by_rer: dict[str, list[str]] = defaultdict(list)
    for rer, direction in volume:
        directions_by_rer[rer].append(direction)

    opposite: dict[tuple[str, str], dict[str, float]] = {}
    for rer, directions in directions_by_rer.items():
        for direction in directions:
            combined: dict[str, float] = defaultdict(float)
            for other in directions:
                if other == direction:
                    continue
                for station, count in volume[(rer, other)].items():
                    combined[station] += count
            opposite[(rer, direction)] = dict(combined)
    return opposite


def _attractiveness(stations: list[str], opposite: dict[str, float]) -> list[float]:
    raw = [max(opposite.get(station, 0.0), 0.0) for station in stations]
    positive = [value for value in raw if value > 0]
    if not positive:
        return [1.0] * len(stations)
    floor = 0.02 * (sum(positive) / len(positive))
    return [value if value > 0 else floor for value in raw]


def _destination_probabilities(attractiveness: list[float]) -> list[list[float]]:
    count = len(attractiveness)
    probabilities = [[0.0] * count for _ in range(count)]
    for origin in range(count):
        downstream = attractiveness[origin + 1 :]
        total = sum(downstream)
        if total <= 0:
            if origin < count - 1:
                probabilities[origin][-1] = 1.0
            continue
        for offset, weight in enumerate(downstream):
            probabilities[origin][origin + 1 + offset] = weight / total
    return probabilities


def _assign_boardings(
    departures: list[tuple[str, int]],
    start: float,
    end: float,
    boardings: float,
) -> tuple[dict[str, float], float, float, list[tuple[float, float, float]]]:
    """Répartit des arrivées uniformes sur [start, end) vers le prochain train."""
    if boardings == 0 or end <= start:
        return {}, 0.0, 0.0, []
    if not departures:
        return {}, 0.0, boardings, []

    groups: list[tuple[int, list[str]]] = []
    for course_id, minute in departures:
        if groups and groups[-1][0] == minute:
            groups[-1][1].append(course_id)
        else:
            groups.append((minute, [course_id]))

    assigned: dict[str, float] = defaultdict(float)
    pieces: list[tuple[float, float, float]] = []
    total_wait = 0.0
    span = end - start
    previous: float | None = None
    for minute, course_ids in groups:
        left = start if previous is None else max(start, previous)
        right = min(end, float(minute))
        if right > left:
            count = boardings * (right - left) / span
            share = count / len(course_ids)
            mean_wait = minute - (left + right) / 2
            low = minute - right
            high = minute - left
            for course_id in course_ids:
                assigned[course_id] += share
                total_wait += share * mean_wait
                pieces.append((low, high, share))
        previous = float(minute)

    assert previous is not None
    left = max(start, previous)
    missed = boardings * (end - left) / span if end > left else 0.0
    return assigned, total_wait, missed, pieces


def _mixture_quantile(pieces: list[tuple[float, float, float]], quantile: float) -> float | None:
    """Quantile d'un mélange de lois uniformes (low, high, poids)."""
    total = sum(weight for _low, _high, weight in pieces if weight > 0)
    if total <= 0:
        return None
    target = quantile * total
    events: list[tuple[float, int, float]] = []
    for low, high, weight in pieces:
        if weight <= 0:
            continue
        if high <= low:
            events.append((low, 1, weight))
            continue
        density = weight / (high - low)
        events.append((low, 2, density))
        events.append((high, 0, density))
    events.sort()

    density = 0.0
    cumulative = 0.0
    previous: float | None = None
    for instant, kind, value in events:
        if previous is not None and instant > previous and density > 0:
            gain = density * (instant - previous)
            if cumulative + gain >= target:
                return previous + (target - cumulative) / density
            cumulative += gain
        previous = instant
        if kind == 0:
            density = max(0.0, density - value)
        elif kind == 1:
            if cumulative + value >= target:
                return instant
            cumulative += value
        else:
            density += value
    return events[-1][0] if events else None
