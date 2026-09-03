import unittest

import numpy as np

from associator_pal import PS_Pair_Assoc


class AssociatorUniqueStationTests(unittest.TestCase):
    def make_associator(self, min_sta=3):
        associator = PS_Pair_Assoc.__new__(PS_Pair_Assoc)
        associator.tt_dict = {
            station: np.zeros((1, 1, 1), dtype=float)
            for station in ("A", "B", "C")
        }
        associator.max_res = 1.2
        associator.min_sta = min_sta
        associator.lon_min = -118.0
        associator.lat_min = 34.0
        associator.xy_grid = 0.02
        associator.z_grids = np.asarray([5.0])
        return associator

    def rows(self, values):
        dtype = np.dtype([
            ("net_sta", object), ("sta_ot", float),
            ("tp", float), ("ts", float),
        ])
        return np.asarray(values, dtype=dtype)

    def test_alternative_pairs_do_not_inflate_station_count(self):
        associator = self.make_associator(min_sta=3)
        picks = self.rows([
            ("A", 0.0, 0.1, 1.0),
            ("A", 0.0, 0.2, 1.1),
            ("A", 0.0, 0.3, 1.2),
            ("B", 0.0, 0.1, 1.0),
        ])
        location, selected, _, _ = associator.assoc_loc(
            picks, unique_stations=True
        )
        self.assertEqual(location, [])
        self.assertEqual(selected, [])

    def test_best_pair_per_station_is_selected_at_location(self):
        associator = self.make_associator(min_sta=3)
        picks = self.rows([
            ("A", 0.0, 0.1, 1.0),
            ("A", 0.0, 0.9, 1.8),
            ("B", 0.0, 0.2, 1.1),
            ("C", 0.0, 0.3, 1.2),
        ])
        location, selected, _, _ = associator.assoc_loc(
            picks, unique_stations=True
        )
        self.assertTrue(location)
        self.assertEqual(len(selected), 3)
        self.assertEqual(len({row["net_sta"] for row in selected}), 3)

    def test_magnitude_uses_valid_repicker_amplitudes_only(self):
        associator = self.make_associator(min_sta=3)
        associator.sta_dict = {
            "A": [34.0, -118.0, 0.0, 1.0],
            "B": [34.1, -118.0, 0.0, 1.0],
            "C": [34.2, -118.0, 0.0, 1.0],
        }
        dtype = np.dtype([("net_sta", object), ("s_amp", float)])
        picks = np.asarray([
            ("A", 1.0e-6),
            ("B", -1.0),
            ("C", np.nan),
        ], dtype=dtype)
        location = {
            "evt_lat": 34.0, "evt_lon": -118.0, "evt_dep": 5.0,
        }
        result = associator.calc_mag(picks, location)
        self.assertAlmostEqual(result["mag"], round(np.log10(5.0) + 1.0, 2))

    def test_magnitude_is_missing_without_valid_amplitudes(self):
        associator = self.make_associator(min_sta=3)
        associator.sta_dict = {"A": [34.0, -118.0, 0.0, 1.0]}
        dtype = np.dtype([("net_sta", object), ("s_amp", float)])
        picks = np.asarray([("A", -1.0)], dtype=dtype)
        location = {
            "evt_lat": 34.0, "evt_lon": -118.0, "evt_dep": 5.0,
        }
        self.assertEqual(associator.calc_mag(picks, location)["mag"], -1.0)


if __name__ == "__main__":
    unittest.main()
