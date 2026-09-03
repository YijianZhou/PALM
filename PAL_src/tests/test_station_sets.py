"""Tests for full-network and optional-subnetwork station selection."""
from pathlib import Path
import sys
import unittest

PAL_SRC = Path(__file__).resolve().parents[1]
if str(PAL_SRC) not in sys.path:
    sys.path.insert(0, str(PAL_SRC))

from station_sets import association_station_file_mapping, build_station_union


class DummyConfig(object):
    subnet_assoc_params = {
        "default": {}, "full": {}, "r1": {}, "r2": {}, "r3": {},
    }


class StationSetsTest(unittest.TestCase):
    def test_no_optional_subnets_uses_full(self):
        mapping = association_station_file_mapping(
            DummyConfig(), "full.sta", []
        )
        self.assertEqual(mapping, {"full": Path("full.sta")})

    def test_optional_files_follow_configured_subnet_order(self):
        mapping = association_station_file_mapping(
            DummyConfig(), "full.sta", ["north.sta", "south.sta"]
        )
        self.assertEqual(list(mapping), ["r1", "r2"])
        self.assertEqual(mapping["r1"], Path("north.sta"))
        self.assertEqual(mapping["r2"], Path("south.sta"))
        self.assertNotIn("full", mapping)

    def test_one_optional_file_maps_to_first_subnet(self):
        mapping = association_station_file_mapping(
            DummyConfig(), "full.sta", ["north.sta"]
        )
        self.assertEqual(mapping, {"r1": Path("north.sta")})

    def test_none_full_file_requires_subnets(self):
        with self.assertRaises(ValueError):
            association_station_file_mapping(DummyConfig(), None, [])

    def test_station_union_deduplicates_identical_selectors(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            north = root / "north.sta"
            south = root / "south.sta"
            output = root / "union.sta"
            north.write_text(
                "CI.AAA.HH,34,-118,0,1\nCI.BBB.HH,35,-119,0,2\n",
                encoding="utf-8",
            )
            south.write_text(
                "CI.BBB.HH,35,-119,0,2\nCI.CCC.HH,36,-120,0,3\n",
                encoding="utf-8",
            )
            build_station_union([north, south], output)
            rows = [
                line for line in output.read_text(encoding="utf-8").splitlines()
                if line and not line.startswith("#")
            ]
            self.assertEqual(len(rows), 3)
            self.assertEqual(rows[0].split(",", 1)[0], "CI.AAA.HH")

    def test_station_union_rejects_conflicting_metadata(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            left = root / "left.sta"
            right = root / "right.sta"
            left.write_text("CI.AAA.HH,34,-118,0,1\n", encoding="utf-8")
            right.write_text("CI.AAA.HH,35,-118,0,1\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                build_station_union([left, right], root / "union.sta")


if __name__ == "__main__":
    unittest.main()
