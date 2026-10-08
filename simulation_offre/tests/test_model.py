import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from simulation_offre.cli import main
from simulation_offre.io_csv import load_inputs
from simulation_offre.model import Addition, Demand, Rate, Stop, apply_changes, simulate
from simulation_offre.sample import KWH_PER_COURSE, build_world


def _stops(courses: dict[str, list[tuple[str, int, int]]], direction: str = "Aller") -> list[Stop]:
    stops = []
    for course_id, calls in courses.items():
        for station, ordre, minute in calls:
            stops.append(Stop(course_id, "B", direction, station, ordre, minute))
    return stops


class WaitAndEnergyTest(unittest.TestCase):
    def test_regular_headway_waits_half_the_interval(self):
        stops = _stops(
            {
                "T0": [("A", 0, 0), ("B", 1, 5)],
                "T10": [("A", 0, 10), ("B", 1, 15)],
                "T20": [("A", 0, 20), ("B", 1, 25)],
            }
        )
        demand = [Demand("B", "Aller", "A", 0, 20, 100)]
        result = simulate([Rate("B", 100, 500)], stops, demand)

        self.assertAlmostEqual(result.mean_wait_min, 5)
        self.assertAlmostEqual(result.p90_wait_min, 9)
        self.assertAlmostEqual(result.energy_kwh, 300)
        self.assertAlmostEqual(result.unserved, 0)
        self.assertAlmostEqual(result.served, 100)
        self.assertAlmostEqual(result.boarded, result.alighted)
        self.assertAlmostEqual(result.max_load, 50)

    def test_removing_a_train_lengthens_the_wait_and_saves_its_energy(self):
        stops = _stops(
            {
                "T0": [("A", 0, 0), ("B", 1, 5)],
                "T10": [("A", 0, 10), ("B", 1, 15)],
                "T20": [("A", 0, 20), ("B", 1, 25)],
            }
        )
        demand = [Demand("B", "Aller", "A", 0, 20, 100)]
        rates = [Rate("B", 100, 500)]
        changed, added = apply_changes(stops, {"T10"}, [])
        result = simulate(rates, changed, demand, added)

        self.assertEqual(added, [])
        self.assertAlmostEqual(result.mean_wait_min, 10)
        self.assertAlmostEqual(result.p90_wait_min, 18)
        self.assertAlmostEqual(result.energy_kwh, 200)
        self.assertAlmostEqual(result.served, 100)
        self.assertAlmostEqual(result.max_load, 100)

    def test_adding_a_train_costs_one_course_and_shortens_the_wait(self):
        stops = _stops(
            {
                "T0": [("A", 0, 0), ("B", 1, 5)],
                "T10": [("A", 0, 10), ("B", 1, 15)],
                "T20": [("A", 0, 20), ("B", 1, 25)],
            }
        )
        demand = [Demand("B", "Aller", "A", 0, 20, 100)]
        rates = [Rate("B", 80, 500)]
        changed, added = apply_changes(stops, set(), [Addition("B", "Aller", 5)])
        result = simulate(rates, changed, demand, added)

        self.assertEqual(added, ["ajout-B-Aller-0005"])
        self.assertAlmostEqual(result.energy_kwh, 320)
        self.assertLess(result.mean_wait_min, 5)
        self.assertTrue(any(train.added and train.course_id == added[0] for train in result.trains))

    def test_passengers_after_the_last_train_are_left_behind(self):
        stops = _stops({"T10": [("A", 0, 10), ("B", 1, 15)]})
        demand = [Demand("B", "Aller", "A", 0, 30, 90)]
        result = simulate([Rate("B", 10, 50)], stops, demand)

        self.assertAlmostEqual(result.served, 30)
        self.assertAlmostEqual(result.unserved, 60)
        self.assertAlmostEqual(result.mean_wait_min, 5)

    def test_a_train_that_skips_a_station_does_not_pick_up_its_passengers(self):
        stops = _stops(
            {
                "omnibus": [("A", 0, 0), ("B", 1, 5), ("C", 2, 10)],
                "direct": [("A", 0, 10), ("C", 2, 16)],
            }
        )
        demand = [Demand("B", "Aller", "B", 0, 20, 100)]
        result = simulate([Rate("B", 10, 200)], stops, demand)
        by_id = {train.course_id: train for train in result.trains}

        self.assertAlmostEqual(by_id["omnibus"].boardings, 25)
        self.assertAlmostEqual(by_id["direct"].boardings, 0)
        self.assertAlmostEqual(result.unserved, 75)

    def test_no_train_means_no_energy_and_nobody_boards(self):
        demand = [Demand("B", "Aller", "A", 0, 30, 40)]
        result = simulate([Rate("B", 10, 100)], [], demand)

        self.assertEqual(result.energy_kwh, 0)
        self.assertIsNone(result.mean_wait_min)
        self.assertAlmostEqual(result.unserved, 40)
        self.assertEqual(result.unknown_stations, ("B / Aller / A",))

    def test_unknown_course_is_rejected(self):
        stops = _stops({"T0": [("A", 0, 0), ("B", 1, 5)]})
        with self.assertRaises(ValueError):
            apply_changes(stops, {"absent"}, [])

    def test_load_is_conserved_with_both_directions(self):
        stops = _stops(
            {"A1": [("A", 0, 30), ("B", 1, 35), ("C", 2, 40)]},
            "Aller",
        ) + _stops(
            {"R1": [("C", 0, 30), ("B", 1, 35), ("A", 2, 40)]},
            "Retour",
        )
        demand = [
            Demand("B", "Aller", "A", 0, 30, 60),
            Demand("B", "Aller", "B", 0, 30, 30),
            Demand("B", "Retour", "C", 0, 30, 80),
            Demand("B", "Retour", "B", 0, 30, 20),
        ]
        result = simulate([Rate("B", 50, 40)], stops, demand)

        self.assertAlmostEqual(result.boarded, result.alighted)
        self.assertAlmostEqual(result.terminus_boardings, 0)
        self.assertGreater(result.over_capacity, 0)
        self.assertAlmostEqual(result.served, 190)


class CsvAndSampleTest(unittest.TestCase):
    def test_semicolon_headers_and_decimal_comma(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "consommations.csv").write_text(
                "ligne;consommation;capacité\nB;270,5;1 050\n",
                encoding="utf-8",
            )
            (root / "plan.csv").write_text(
                "course;rer;sens;gare;ordre;heure\nT1;B;Paris;Robinson;0;07:00\n"
                "T1;B;Paris;Luxembourg;2;07:20\n",
                encoding="utf-8",
            )
            (root / "affluence.csv").write_text(
                "rer;direction;station;tranche;affluence\nB;Paris;Robinson;07:00;1.234,5\n",
                encoding="utf-8",
            )
            rates, stops, demand = load_inputs(
                root / "consommations.csv", root / "plan.csv", root / "affluence.csv"
            )

        self.assertAlmostEqual(rates[0].kwh_per_course, 270.5)
        self.assertAlmostEqual(rates[0].capacity, 1050)
        self.assertEqual(stops[0].course_id, "T1")
        self.assertEqual(demand[0].station, "Robinson")
        self.assertEqual(demand[0].end - demand[0].start, 30)
        self.assertAlmostEqual(demand[0].boardings, 1234.5)

    def test_dated_affluence_keeps_the_time_of_day(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "consommations.csv").write_text(
                "rer,kwh_par_course,capacite\nA,700,2600\n",
                encoding="utf-8",
            )
            (root / "plan.csv").write_text(
                "course_id,rer,direction,station,ordre,heure\n"
                "T1,A,est,AUBER,0,23:40\n",
                encoding="utf-8",
            )
            (root / "affluence.csv").write_text(
                "rer,direction,station,debut_tranche,fin_tranche,montees\n"
                "A,est,AUBER,2024-01-25 08:00:00,2024-01-25 08:30:00,10\n"
                "A,est,AUBER,2024-01-25 23:30:00,2024-01-26 00:00:00,4\n",
                encoding="utf-8",
            )
            _rates, _stops, demand = load_inputs(
                root / "consommations.csv", root / "plan.csv", root / "affluence.csv"
            )

        self.assertEqual(
            [(row.day, row.start, row.end) for row in demand],
            [
                ("2024-01-25", 8 * 60, 8 * 60 + 30),
                ("2024-01-25", 23 * 60 + 30, 24 * 60),
            ],
        )

    def test_sample_day_removal_saves_one_course_and_increases_wait(self):
        rates, stops, demand = build_world()
        baseline = simulate(rates, stops, demand)
        target = "A-Saint-Germain-en-Laye-0800"
        self.assertIn(target, {stop.course_id for stop in stops})
        changed, _added = apply_changes(stops, {target}, [])
        scenario = simulate(rates, changed, demand)

        self.assertAlmostEqual(baseline.energy_kwh - scenario.energy_kwh, KWH_PER_COURSE)
        self.assertGreater(scenario.mean_wait_min, baseline.mean_wait_min)
        self.assertAlmostEqual(scenario.served, baseline.served)
        self.assertAlmostEqual(baseline.boarded, baseline.alighted, places=4)
        self.assertGreater(baseline.max_load, 0)

    def test_cli_prints_the_energy_delta(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            from simulation_offre.sample import write_csvs

            write_csvs(root)
            buffer = io.StringIO()
            with patch("sys.stdout", buffer):
                status = main(
                    [
                        "simuler",
                        "--consommations",
                        str(root / "consommations.csv"),
                        "--plan",
                        str(root / "plan_transport.csv"),
                        "--affluence",
                        str(root / "affluence.csv"),
                        "--retirer",
                        "A-Saint-Germain-en-Laye-0800",
                        "--json",
                    ]
                )
        self.assertEqual(status, 0)
        document = json.loads(buffer.getvalue())
        self.assertEqual(document["ecart"]["energie_kwh"], -KWH_PER_COURSE)


if __name__ == "__main__":
    unittest.main()
