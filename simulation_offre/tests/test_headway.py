import unittest

from simulation_offre.headway import even_times, headway_changes, mean_headway
from simulation_offre.model import apply_changes, simulate
from simulation_offre.sample import KWH_PER_COURSE, build_world


class HeadwayTest(unittest.TestCase):
    def test_even_times_keep_a_constant_gap(self):
        self.assertEqual(even_times(7 * 60, 9 * 60 + 54, 10), list(range(7 * 60, 9 * 60 + 51, 10)))

    def test_mean_headway_uses_the_gaps(self):
        self.assertAlmostEqual(mean_headway([0, 6, 12, 24]), 8)

    def test_constant_morning_interval_changes_energy_by_whole_courses(self):
        rates, stops, demand = build_world()
        baseline = simulate(rates, stops, demand)
        removed, additions = headway_changes(stops, "A", {"matin": 10, "soir": None, "tout": None})
        changed, added = apply_changes(stops, removed, additions)
        scenario = simulate(rates, changed, demand, added)

        delta = baseline.energy_kwh - scenario.energy_kwh
        self.assertGreater(delta, 0)
        self.assertAlmostEqual(delta / KWH_PER_COURSE, round(delta / KWH_PER_COURSE))
        self.assertGreater(scenario.mean_wait_min, baseline.mean_wait_min)

        kept = {
            stop.minute
            for stop in changed
            if stop.direction == "Saint-Germain-en-Laye" and stop.ordre == 0
        }
        morning = sorted(minute for minute in kept if 7 * 60 <= minute < 10 * 60)
        gaps = {right - left for left, right in zip(morning, morning[1:])}
        self.assertEqual(gaps, {10})

    def test_unset_headway_keeps_the_plan(self):
        _rates, stops, _demand = build_world()
        removed, additions = headway_changes(
            stops, "A", {"matin": None, "soir": None, "tout": None}
        )
        self.assertEqual(removed, set())
        self.assertEqual(additions, [])


if __name__ == "__main__":
    unittest.main()
