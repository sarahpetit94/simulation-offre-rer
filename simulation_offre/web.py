"""Interface locale : charger les CSV, retirer ou ajouter des courses."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from simulation_offre.io_csv import load_demand, load_rates, load_stops, parse_csv
from simulation_offre.model import Addition, Demand, Rate, Stop, apply_changes, simulate
from simulation_offre.present import catalogue, ecart, format_minute, payload
from simulation_offre.sample import build_world

_HTML = (Path(__file__).resolve().parent / "index.html").read_text(encoding="utf-8")
_session: dict | None = None


def serve(port: int) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Simulation d'offre — http://127.0.0.1:{port}")
    print("Arrêt : Ctrl+C")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/":
            self._bytes(200, _HTML.encode("utf-8"), "text/html; charset=utf-8")
            return
        self._json(404, {"error": "Page introuvable"})

    def do_POST(self) -> None:
        try:
            body = self._body()
            if self.path == "/api/exemple":
                result = _store(*build_world())
            elif self.path == "/api/charger":
                result = _store(*_from_texts(body))
            elif self.path == "/api/simuler":
                result = _simulate(body)
            else:
                self._json(404, {"error": "Page introuvable"})
                return
            self._json(200, result)
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
        except Exception as exc:
            self._json(500, {"error": str(exc)})

    def log_message(self, fmt: str, *args) -> None:
        return

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        if not raw.strip():
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("Requête JSON illisible") from exc
        if not isinstance(parsed, dict):
            raise ValueError("Requête JSON illisible")
        return parsed

    def _json(self, status: int, document: dict) -> None:
        encoded = json.dumps(document, ensure_ascii=False).encode("utf-8")
        self._bytes(status, encoded, "application/json; charset=utf-8")

    def _bytes(self, status: int, content: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)


def _from_texts(body: dict) -> tuple[list[Rate], list[Stop], list[Demand]]:
    try:
        rates = load_rates(parse_csv(body["consommations"], "consommations"))
        stops = load_stops(parse_csv(body["plan"], "plan de transport"))
        demand = load_demand(parse_csv(body["affluence"], "affluence"))
    except KeyError as exc:
        raise ValueError("Il manque un des trois fichiers") from exc
    return rates, stops, demand


def _store(rates: list[Rate], stops: list[Stop], demand: list[Demand]) -> dict:
    global _session
    baseline = simulate(rates, stops, demand)
    _session = {
        "rates": rates,
        "stops": stops,
        "demand": demand,
        "baseline": baseline,
    }
    return _pack([], [])


def _simulate(body: dict) -> dict:
    if _session is None:
        raise ValueError("Chargez d'abord les trois fichiers, ou l'exemple.")
    removed = body.get("retirees") or []
    raw_additions = body.get("ajoutees") or []
    if not isinstance(removed, list) or not isinstance(raw_additions, list):
        raise ValueError("Le scénario est illisible")
    additions = []
    for item in raw_additions:
        if not isinstance(item, dict):
            raise ValueError("Ajout illisible")
        try:
            additions.append(
                Addition(
                    rer=str(item["rer"]),
                    direction=str(item["direction"]),
                    minute=int(item["minute"]),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Ajout illisible : RER, sens et minute sont requis") from exc
    return _pack([str(course_id) for course_id in removed], additions)


def _pack(removed: list[str], additions: list[Addition]) -> dict:
    assert _session is not None
    rates = _session["rates"]
    stops = _session["stops"]
    demand = _session["demand"]
    baseline = _session["baseline"]
    changed, added_ids = apply_changes(stops, set(removed), additions)
    scenario = simulate(rates, changed, demand, added_ids)
    added_rows = [row for row in catalogue(changed) if row["id"] in set(added_ids)]
    order = {course_id: index for index, course_id in enumerate(added_ids)}
    added_rows.sort(key=lambda row: order[row["id"]])
    for row, addition in zip(added_rows, additions):
        row["heure"] = format_minute(addition.minute)
        row["minute"] = addition.minute
    return {
        "courses": catalogue(stops),
        "ajouts": added_rows,
        "plan": payload(baseline),
        "scenario": payload(scenario),
        "ecart": ecart(baseline, scenario),
    }
