"""Ligne de commande de la simulation d'offre."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from simulation_offre.io_csv import _clock, load_inputs
from simulation_offre.model import Addition, apply_changes, simulate
from simulation_offre.present import ecart, format_report, payload
from simulation_offre.sample import write_csvs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m simulation_offre",
        description=(
            "Simule la consommation, les temps d'attente et la charge à bord "
            "d'une journée de RER, et compare le plan prévu à un scénario "
            "où des courses sont retirées ou ajoutées."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sim = sub.add_parser("simuler", help="Comparer le plan prévu à un scénario")
    sim.add_argument("--consommations", required=True, type=Path)
    sim.add_argument("--plan", required=True, type=Path)
    sim.add_argument("--affluence", required=True, type=Path)
    sim.add_argument(
        "--retirer",
        action="append",
        default=[],
        metavar="COURSE",
        help="Identifiant de course à retirer. Répéter l'option pour en retirer plusieurs.",
    )
    sim.add_argument(
        "--ajouter",
        action="append",
        default=[],
        metavar="SENS,HH:MM",
        help=(
            "Départ à ajouter à l'origine du sens, calqué sur la course la plus complète. "
            "Exemple : La Défense,08:03. S'il y a plusieurs RER : A,La Défense,08:03."
        ),
    )
    sim.add_argument("--json", action="store_true", help="Écrire le résultat en JSON")

    serve = sub.add_parser("servir", help="Ouvrir l'interface dans le navigateur local")
    serve.add_argument("--port", type=int, default=8765)

    example = sub.add_parser(
        "ecrire-exemple",
        help="Écrire les trois CSV d'exemple (RER A, La Défense – Chessy)",
    )
    example.add_argument(
        "dossier",
        nargs="?",
        type=Path,
        help="Dossier de sortie. Par défaut : simulation_offre/exemples",
    )

    args = parser.parse_args(argv)
    try:
        if args.command == "simuler":
            return _simulate_command(args)
        if args.command == "servir":
            from simulation_offre.web import serve as serve_app

            serve_app(args.port)
            return 0
        directory = args.dossier or Path(__file__).resolve().parent / "exemples"
        write_csvs(directory)
        print(f"Exemple écrit dans {directory}")
        return 0
    except (ValueError, OSError) as exc:
        print(exc, file=sys.stderr)
        return 1


def _simulate_command(args: argparse.Namespace) -> int:
    rates, stops, demand = load_inputs(args.consommations, args.plan, args.affluence)
    additions, labels = _additions(args.ajouter, stops)
    baseline = simulate(rates, stops, demand)
    changed, added_ids = apply_changes(stops, set(args.retirer), additions)
    scenario = simulate(rates, changed, demand, added_ids)
    if args.json:
        document = {
            "plan": payload(baseline),
            "scenario": payload(scenario),
            "ecart": ecart(baseline, scenario),
            "courses_retirees": list(args.retirer),
            "courses_ajoutees": [
                {"id": course_id, "libelle": label} for course_id, label in zip(added_ids, labels)
            ],
        }
        print(json.dumps(document, ensure_ascii=False, indent=2))
    else:
        print(format_report(baseline, scenario, list(args.retirer), labels))
    return 0


def _additions(texts: list[str], stops) -> tuple[list[Addition], list[str]]:
    rers = sorted({stop.rer for stop in stops})
    additions: list[Addition] = []
    labels: list[str] = []
    for text in texts:
        parts = [part.strip() for part in text.split(",") if part.strip()]
        if len(parts) == 2:
            if len(rers) != 1:
                joined = ", ".join(rers)
                raise ValueError(
                    f"Plusieurs RER ({joined}) : écrivez RER,sens,HH:MM pour « {text} »"
                )
            direction, heure = parts
            rer = rers[0]
        elif len(parts) == 3:
            rer, direction, heure = parts
        else:
            raise ValueError(f"Ajout invalide « {text} ». Format : sens,HH:MM")
        minute = _clock(heure, text)
        additions.append(Addition(rer=rer, direction=direction, minute=minute))
        labels.append(f"{rer} {direction} {heure}")
    return additions, labels
