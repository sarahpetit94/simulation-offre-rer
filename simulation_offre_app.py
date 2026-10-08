"""Application Streamlit de la simulation d'offre.

Depuis le dossier data_pipeline :

    pip install streamlit
    streamlit run simulation_offre_app.py
"""

from __future__ import annotations

import importlib
from dataclasses import replace
from datetime import date
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import simulation_offre.model as offer_model
from simulation_offre.headway import headway_changes, in_period, mean_headway
from simulation_offre.io_csv import load_inputs
from simulation_offre.present import catalogue

# Streamlit relance ce fichier sans recharger les modules déjà importés.
if not hasattr(offer_model.TrainStat, "segment_loads"):
    importlib.reload(offer_model)

Demand = offer_model.Demand
apply_changes = offer_model.apply_changes
simulate = offer_model.simulate

EXEMPLES = Path(__file__).resolve().parent / "simulation_offre" / "exemples"
INPUTS = (
    EXEMPLES / "consommations.csv",
    EXEMPLES / "plan_transport.csv",
    EXEMPLES / "affluence.csv",
)

st.set_page_config(page_title="Simulation d'offre RER", layout="wide")

PERIOD_LABELS = {
    "matin": "Pointe du matin",
    "soir": "Pointe du soir",
    "tout": "Toute la journée",
}
BOISSY = {
    "Vincennes",
    "Fontenay-sous-Bois",
    "Nogent-sur-Marne",
    "Joinville-le-Pont",
    "Saint-Maur-Créteil",
    "Le Parc de Saint-Maur",
    "Champigny",
    "La Varenne-Chennevières",
    "Sucy-Bonneuil",
    "Boissy-Saint-Léger",
}
STATIONS = {
    "LA DEFENSE": "La Défense",
    "ETOILE": "Charles de Gaulle-Étoile",
    "AUBER": "Auber",
    "CHATELET LES HALLES": "Châtelet-Les Halles",
    "GARE DE LYON": "Gare de Lyon",
    "NATION": "Nation",
    "SAINT GERMAIN EN LAYE": "Saint-Germain-en-Laye",
    "LE VESINET LE PECQ": "Le Vésinet-Le Pecq",
    "LE VESINET CENTRE": "Le Vésinet-Centre",
    "CHATOU CROISSY": "Chatou-Croissy",
    "RUEIL MALMAISON": "Rueil-Malmaison",
    "NANTERRE VILLE": "Nanterre-Ville",
    "NANTERRE UNIVERSITE": "Nanterre-Université",
    "NANTERRE PREFECTURE": "Nanterre-Préfecture",
    "VINCENNES": "Vincennes",
    "FONTENAY SOUS BOIS": "Fontenay-sous-Bois",
    "NOGENT SUR MARNE": "Nogent-sur-Marne",
    "JOINVILLE LE PONT": "Joinville-le-Pont",
    "SAINT MAUR CRETEIL": "Saint-Maur-Créteil",
    "LE PARC DE SAINT MAUR": "Le Parc de Saint-Maur",
    "CHAMPIGNY": "Champigny",
    "LA VARENNE CHENNEVIERES": "La Varenne-Chennevières",
    "SUCY BONNEUIL": "Sucy-Bonneuil",
    "BOISSY SAINT LEGER": "Boissy-Saint-Léger",
    "VAL DE FONTENAY": "Val de Fontenay",
    "NEUILLY PLAISANCE": "Neuilly-Plaisance",
    "BRY SUR MARNE": "Bry-sur-Marne",
    "NOISY LE GRAND MONT DEST": "Noisy-le-Grand",
    "NOISY CHAMPS": "Noisy-Champs",
    "NOISIEL": "Noisiel",
    "LOGNES": "Lognes",
    "TORCY": "Torcy",
    "BUSSY SAINT GEORGES": "Bussy-Saint-Georges",
    "VAL DEUROPE": "Val d'Europe",
    "MARNE LA VALLEE CHESSY": "Marne-la-Vallée-Chessy",
}
_JOURS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
_MOIS = (
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
)


def _fr(value: float | None, digits: int) -> str:
    if value is None:
        return "—"
    text = f"{value:,.{digits}f}"
    return text.replace(",", " ").replace(".", ",")


def _signed(value: float, digits: int) -> str:
    text = _fr(abs(value), digits)
    if value > 0:
        return "+" + text
    if value < 0:
        return "-" + text
    return text


def _minutes(courses: list[dict], rer: str, direction: str, period: str) -> list[int]:
    return [
        course["minute"]
        for course in courses
        if course["rer"] == rer
        and course["direction"] == direction
        and in_period(course["minute"], period)
    ]


def _choice(minutes: list[int], key: str) -> int | None:
    mean = mean_headway(minutes)
    planned = min(30, max(3, round(mean)))
    value = st.slider(
        "Intervalle",
        min_value=3,
        max_value=30,
        value=planned,
        step=1,
        key=key,
        label_visibility="collapsed",
    )
    if value == planned:
        st.caption(
            f"Plan prévu · {_fr(mean, 1)} min en moyenne. Une autre valeur aligne les départs."
        )
        return None
    st.caption(
        f"Écart de {value} min à partir du premier train. "
        f"Revenir sur {planned} min retrouve le plan prévu."
    )
    return value


def _stations(result, rer: str, direction: str) -> list:
    return [
        station
        for station in result.stations
        if station.rer == rer and station.direction == direction
    ]


def _value(station, attribute: str) -> float:
    if station is None:
        return 0
    value = getattr(station, attribute)
    return 0 if value is None else value


def _capacity(result, rer: str, direction: str) -> float | None:
    for train in result.trains:
        if train.rer == rer and train.direction == direction and train.capacity:
            return train.capacity
    return None


def _overloaded(result, rer: str, direction: str, period: str) -> int:
    return sum(
        1
        for train in result.trains
        if train.rer == rer
        and train.direction == direction
        and train.over_capacity
        and in_period(train.origin_minute, period)
    )


def _for_period(stations: list, trains, rer: str, direction: str, period: str) -> list:
    selected = [
        train
        for train in trains
        if train.rer == rer and train.direction == direction and in_period(train.origin_minute, period)
    ]
    scoped = []
    for index, station in enumerate(stations):
        if station.max_load is None:
            scoped.append(station)
            continue
        peaks = [
            train.segment_loads[index]
            for train in selected
            if index < len(train.segment_loads)
        ]
        peak = max(peaks) if peaks else 0.0
        scoped.append(replace(station, max_load=peak))
    return scoped


def _overload_text(count: int, direction: str, period: str) -> str | None:
    if not count:
        return None
    noun = "courses dépassent" if count > 1 else "course dépasse"
    when = "" if period == "tout" else f" {PERIOD_LABELS[period].lower()}"
    return f"{count} {noun} la capacité vers {direction}{when}."


def _bars(
    plan_rows,
    scenario_rows,
    attribute: str,
    unit: str,
    reference: float | None = None,
    reference_name: str = "",
) -> None:
    names = [
        station.station for station in scenario_rows if getattr(station, attribute) is not None
    ]
    plan_by_name = {station.station: station for station in plan_rows}
    scenario_by_name = {station.station: station for station in scenario_rows}
    plan_values = [_value(plan_by_name.get(name), attribute) for name in names]
    scenario_values = [_value(scenario_by_name.get(name), attribute) for name in names]
    figure = go.Figure()
    figure.add_bar(
        name="Plan prévu",
        y=names,
        x=plan_values,
        orientation="h",
        marker_color="#c8bfb2",
    )
    figure.add_bar(
        name="Scénario",
        y=names,
        x=scenario_values,
        orientation="h",
        marker_color="#0d5c4c",
    )
    peak = max(plan_values + scenario_values, default=0)
    if reference is not None and names:
        figure.add_vline(
            x=reference,
            line_dash="dash",
            line_color="#8a3d12",
            line_width=2,
            annotation_text=f"{reference_name} {_fr(reference, 0)}",
            annotation_position="top left",
            annotation_font_color="#8a3d12",
        )
        peak = max(peak, reference)
    figure.update_layout(
        barmode="group",
        height=max(280, 28 * len(names)),
        margin={"l": 0, "r": 16, "t": 28, "b": 0},
        yaxis={"autorange": "reversed", "title": ""},
        xaxis={"title": unit, "range": [0, peak * 1.08 if peak else 1]},
        legend={"orientation": "h", "y": 1.12},
    )
    st.plotly_chart(figure, width="stretch")


@st.cache_data(show_spinner="Lecture des fichiers…")
def _read(paths: tuple[str, ...], versions: tuple[int, ...]) -> tuple:
    if len(versions) != len(paths):
        raise ValueError("Lecture des fichiers impossible")
    return load_inputs(*(Path(path) for path in paths))


def _jour(iso: str) -> str:
    moment = date.fromisoformat(iso)
    return f"{_JOURS[moment.weekday()]} {moment.day} {_MOIS[moment.month - 1]} {moment.year}"


def _direction(raw: str) -> str:
    if raw in ("est", "Marne-la-Vallée"):
        return "Marne-la-Vallée"
    if raw in ("ouest", "La Défense", "Saint-Germain-en-Laye"):
        return "Saint-Germain-en-Laye"
    return raw


def _for_day(demand: list[Demand], day: str | None) -> list[Demand]:
    chosen = []
    for row in demand:
        if day is not None and row.day != day:
            continue
        station = STATIONS.get(row.station, row.station)
        if station in BOISSY:
            continue
        chosen.append(
            Demand(
                row.rer,
                _direction(row.direction),
                station,
                row.start,
                row.end,
                row.boardings,
            )
        )
    return chosen


def _inputs():
    with st.sidebar:
        st.header("Données")
        st.caption("Fichiers lus dans simulation_offre/exemples/")
        for path in INPUTS:
            st.text(path.name)
        st.caption(
            "Une seule branche : Saint-Germain-en-Laye – Marne-la-Vallée. "
            "Les montées de la branche de Boissy ne sont pas prises en compte."
        )
        st.button("Recharger les fichiers")
    missing = [path.name for path in INPUTS if not path.is_file()]
    if missing:
        st.error("Fichiers manquants : " + ", ".join(missing))
        return None
    try:
        versions = tuple(path.stat().st_mtime_ns for path in INPUTS)
        return _read(tuple(str(path) for path in INPUTS), versions)
    except ValueError as exc:
        st.error(str(exc))
        return None


def _indicators(baseline, scenario, overload: str | None) -> None:
    metrics = st.columns(4)
    cards = [
        (
            "Trains de la journée",
            _fr(scenario.n_courses, 0),
            scenario.n_courses - baseline.n_courses,
            "trains",
            0,
        ),
        (
            "Énergie de la journée",
            f"{_fr(scenario.energy_kwh, 0)} kWh",
            scenario.energy_kwh - baseline.energy_kwh,
            "kWh",
            0,
        ),
        (
            "Attente moyenne",
            f"{_fr(scenario.mean_wait_min, 2)} min",
            (scenario.mean_wait_min or 0) - (baseline.mean_wait_min or 0),
            "min",
            2,
        ),
        (
            "Charge maximale",
            _fr(scenario.max_load, 0),
            scenario.max_load - baseline.max_load,
            "",
            0,
        ),
    ]
    for column, (label, value, delta, unit, digits) in zip(metrics, cards):
        suffix = f" {unit}" if unit else ""
        if delta < 0:
            color = "green"
        elif delta > 0:
            color = "red"
        else:
            color = "off"
        column.metric(
            label,
            value,
            delta=f"{_signed(delta, digits)}{suffix}",
            delta_color=color,
        )
    if overload:
        st.warning(overload)
    if scenario.unknown_stations:
        unknown = ", ".join(scenario.unknown_stations)
        st.error("Gares d'affluence absentes du plan : " + unknown)


def main() -> None:
    st.title("Simulation d'offre RER")
    st.write(
        "Comparez le plan de transport prévu à un scénario où l'intervalle entre les courses "
        "est constant. La consommation suit le nombre de courses. L'attente et la charge "
        "suivent les montées prévues."
    )
    loaded = _inputs()
    if loaded is None:
        return
    rates, stops, demand = loaded
    courses = catalogue(stops)
    rers = sorted({course["rer"] for course in courses})
    if not rers:
        st.error("Le plan de transport ne contient aucune course.")
        return

    indicators = st.empty()
    controls, charge = st.columns([1, 2])
    days = sorted({row.day for row in demand if row.day})
    with controls:
        day = None
        if days:
            day = st.selectbox("Jour", days, index=len(days) - 1, format_func=_jour)
        rer = st.selectbox("RER", rers)
        directions = sorted({course["direction"] for course in courses if course["rer"] == rer})
        direction = st.selectbox("Sens affiché", directions)
        demand = _for_day(demand, day)
        st.subheader("Pointe du matin, 7 h – 10 h")
        morning = _choice(_minutes(courses, rer, direction, "matin"), f"intervalle-matin-{rer}")
        st.subheader("Pointe du soir, 16 h – 19 h 30")
        evening = _choice(_minutes(courses, rer, direction, "soir"), f"intervalle-soir-{rer}")
        st.caption(
            "Le même intervalle est appliqué dans les deux sens. "
            "Une course ajoutée reprend la marche de la course la plus complète."
        )

    removed, additions = headway_changes(
        stops, rer, {"matin": morning, "soir": evening, "tout": None}
    )
    changed, added_ids = apply_changes(stops, removed, additions)
    baseline = simulate(rates, stops, demand)
    scenario = simulate(rates, changed, demand, added_ids)
    plan_stations = _stations(baseline, rer, direction)
    scenario_stations = _stations(scenario, rer, direction)

    with charge:
        st.subheader(f"Charge max à bord · {rer} vers {direction}")
        period = st.radio(
            "Période affichée",
            list(PERIOD_LABELS),
            format_func=PERIOD_LABELS.get,
            horizontal=True,
        )
        plan_load = _for_period(plan_stations, baseline.trains, rer, direction, period)
        scenario_load = _for_period(scenario_stations, scenario.trains, rer, direction, period)
        _bars(
            plan_load,
            scenario_load,
            "max_load",
            "Voyageurs",
            reference=_capacity(scenario, rer, direction),
            reference_name="Capacité",
        )
        overload = _overload_text(
            _overloaded(scenario, rer, direction, period), direction, period
        )
        if overload:
            st.caption(overload)

    with indicators.container():
        _indicators(baseline, scenario, overload)
    st.subheader(f"Temps d'attente moyen · {rer} vers {direction}")
    _bars(plan_stations, scenario_stations, "mean_wait_min", "Minutes")
    added = set(added_ids)
    rows = []
    for train in scenario.trains:
        visible = train.rer == rer and train.direction == direction
        visible = visible and in_period(train.origin_minute, period)
        if not visible:
            continue
        hour, minute = divmod(train.origin_minute, 60)
        rows.append(
            {
                "Départ": f"{hour:02d}:{minute:02d}",
                "Origine": train.origin_station,
                "Statut": "Ajoutée" if train.course_id in added else "Prévue",
                "Charge max": round(train.max_load),
                "Capacité dépassée": "Oui" if train.over_capacity else "",
            }
        )
    table = pd.DataFrame(rows)
    if table.empty:
        st.caption("Aucune course dans ce filtre.")
    else:
        st.dataframe(table, width="stretch", hide_index=True)
    st.caption(
        f"{_fr(scenario.unserved, 0)} voyageurs sans train. "
        "Les montées arrivent uniformément et prennent le prochain train. "
        "La charge part vers l'aval au prorata des montées du sens inverse. "
        "Les trains, l'énergie, l'attente et la charge maximale couvrent tous les sens. "
        "Le graphique de charge et les courses en surcharge suivent le sens et la période affichés."
    )


main()
