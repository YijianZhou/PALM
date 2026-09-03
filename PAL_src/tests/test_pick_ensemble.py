"""Focused tests for sliding-window and cross-picker P/S consensus."""
from datetime import datetime
from pathlib import Path
import sys
import tempfile
import unittest

PAL_SRC = Path(__file__).resolve().parents[1]
if str(PAL_SRC) not in sys.path:
    sys.path.insert(0, str(PAL_SRC))

from phase_merge import (
    event_both_group_pick_ratio, merge_group, resolve_pick_provenance,
    select_preferred_provenance_picks,
)


def test_resolve_pick_provenance_uses_final_plot_priority():
    assert resolve_pick_provenance(["pos_only", "initial"]) == "pos_only"
    assert resolve_pick_provenance(["initial|both_groups"]) == "both_groups"
    assert resolve_pick_provenance(["pos_neg_only", "pos_only"]) == "pos_only"


def test_preferred_provenance_selects_matching_station_rows():
    picks = [
        {"pick_provenance": "initial", "marker": 1},
        {"pick_provenance": "pos_neg_only", "marker": 2},
        {"pick_provenance": "both_groups", "marker": 3},
    ]
    provenance, selected = select_preferred_provenance_picks(picks)
    assert provenance == "both_groups"
    assert [pick["marker"] for pick in selected] == [3]


from pick_ensemble import (
    cluster_pair_votes,
    format_pick_row,
    format_picker_cluster_sizes,
    merge_picker_pick_files,
    merge_picker_records,
    read_pick_file,
)


class PickConsensusTest(unittest.TestCase):
    def test_dual_group_provenance_priority_and_ratio(self):
        picks = [
            {"pick_provenance": "initial", "marker": 1},
            {"pick_provenance": "pos_neg_only", "marker": 2},
            {"pick_provenance": "pos_only", "marker": 3},
            {"pick_provenance": "both_groups", "marker": 4},
        ]
        provenance, selected = select_preferred_provenance_picks(picks)
        self.assertEqual(provenance, "both_groups")
        self.assertEqual([pick["marker"] for pick in selected], [4])
        self.assertEqual(event_both_group_pick_ratio({"picks": picks}), 0.25)

    def test_sliding_windows_vote_once_and_report_std(self):
        votes = [
            {"tp": 100.0, "ts": 105.0, "p_prob": 0.8, "s_prob": 0.7, "win_idx": 0},
            # Same window and cluster, but weaker: it must not add support.
            {"tp": 100.1, "ts": 105.1, "p_prob": 0.2, "s_prob": 0.2, "win_idx": 0},
            {"tp": 100.4, "ts": 105.6, "p_prob": 0.6, "s_prob": 0.9, "win_idx": 1},
        ]
        result = cluster_pair_votes(
            votes, tp_dev=1.0, ts_dev=1.5,
            min_support=2, source_field="win_idx",
        )
        self.assertEqual(len(result), 1)
        pick = result[0]
        self.assertEqual(pick["num_support"], 2)
        self.assertEqual(pick["sources"], ["0", "1"])
        self.assertAlmostEqual(pick["tp"], 100.2)
        self.assertAlmostEqual(pick["ts"], 105.3)
        self.assertAlmostEqual(pick["p_prob"], 0.7)
        self.assertAlmostEqual(pick["s_prob"], 0.8)
        self.assertAlmostEqual(pick["tp_std"], 0.2)
        self.assertAlmostEqual(pick["ts_std"], 0.3)
        self.assertAlmostEqual(pick["p_prob_std"], 0.1)
        self.assertAlmostEqual(pick["s_prob_std"], 0.1)
        self.assertEqual(
            cluster_pair_votes(
                votes, 1.0, 1.5, min_support=3, source_field="win_idx"
            ),
            [],
        )

    def test_cross_picker_equal_weight_and_round_trip(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp_dir:
            root = Path(temp_dir)
            files = {}
            inputs = {
                "SAR": {
                    "net_sta": "CI.ABC", "tp": 100.0, "ts": 105.0,
                    "s_amp": 2.0, "p_prob": 0.9, "s_prob": 0.7,
                    "tp_std": 0.2, "ts_std": 0.3,
                    "p_prob_std": 0.04, "s_prob_std": 0.05,
                    "num_support": 10, "sources": ["SAR"],
                },
                "FT": {
                    "net_sta": "CI.ABC", "tp": 100.4, "ts": 105.6,
                    "s_amp": 4.0, "p_prob": 0.5, "s_prob": 0.9,
                    "tp_std": 0.1, "ts_std": 0.2,
                    "p_prob_std": 0.03, "s_prob_std": 0.06,
                    "num_support": 2, "sources": ["FT"],
                },
            }
            for picker, record in inputs.items():
                path = root / "{}.pick".format(picker)
                path.write_text(format_pick_row(record), encoding="utf-8")
                files[picker] = path

            output = root / "ensemble.pick"
            summary = merge_picker_pick_files(
                files, output, tp_dev=1.0, ts_dev=1.5, min_support=2
            )
            self.assertEqual(summary["num_merged_picks"], 1)
            merged = read_pick_file(output)[0]
            # The 10 SAR windows do not outweigh FT: models vote equally.
            self.assertEqual(merged["num_support"], 2)
            self.assertEqual(merged["sources"], ["FT", "SAR"])
            self.assertEqual(
                format_picker_cluster_sizes(merged["picker_cluster_sizes"]),
                "SAR:10|FT:2",
            )
            self.assertAlmostEqual(merged["tp"], 100.2, places=4)
            self.assertAlmostEqual(merged["ts"], 105.3, places=4)
            self.assertAlmostEqual(merged["s_amp"], 3.0)
            self.assertAlmostEqual(merged["p_prob"], 0.7)
            self.assertAlmostEqual(merged["s_prob"], 0.8)
            self.assertAlmostEqual(merged["tp_std"], 0.2)
            self.assertAlmostEqual(merged["ts_std"], 0.3)
            self.assertAlmostEqual(merged["p_prob_std"], 0.2)
            self.assertAlmostEqual(merged["s_prob_std"], 0.1)

    def test_realtime_in_memory_ensemble_requires_distinct_models(self):
        records_by_picker = {
            "SAR": [{
                "net_sta": "CI.ABC", "tp": 100.0, "ts": 105.0,
                "s_amp": 2.0, "p_prob": 0.9, "s_prob": 0.7,
            }],
            "FT": [{
                "net_sta": "CI.ABC", "tp": 100.4, "ts": 105.6,
                "s_amp": 4.0, "p_prob": 0.5, "s_prob": 0.9,
            }],
            "PHN": [{
                "net_sta": "CI.XYZ", "tp": 200.0, "ts": 205.0,
                "s_amp": 1.0, "p_prob": 0.8, "s_prob": 0.8,
            }],
        }
        merged, counts = merge_picker_records(
            records_by_picker, tp_dev=1.0, ts_dev=1.5, min_support=2
        )
        self.assertEqual(counts, {"FT": 1, "PHN": 1, "SAR": 1})
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["net_sta"], "CI.ABC")
        self.assertEqual(merged[0]["sources"], ["FT", "SAR"])
        self.assertEqual(merged[0]["num_support"], 2)
        self.assertEqual(
            format_picker_cluster_sizes(merged[0]["picker_cluster_sizes"]),
            "SAR:1|FT:1",
        )
        self.assertAlmostEqual(merged[0]["tp"], 100.2)
        self.assertAlmostEqual(merged[0]["ts"], 105.3)
    def test_phase_merge_preserves_picker_provenance(self):
        pick_base = {
            "sta": "CI.ABC",
            "p": datetime.fromisoformat("2026-07-01T00:00:01"),
            "s": datetime.fromisoformat("2026-07-01T00:00:04"),
            "score": 3.0,
            "p_prob": 0.7,
            "s_prob": 0.8,
            "tp_std": 0.2,
            "ts_std": 0.3,
            "p_prob_std": 0.2,
            "s_prob_std": 0.1,
            "num_support": 2,
            "sources": "FT|SAR",
            "picker_cluster_sizes": "SAR:4|FT:3|PHN:5|RUN:2",
            "pick_provenance": "both_groups",
            "p_snr_e": 4.0,
            "p_snr_n": 6.0,
            "p_snr_z": 8.0,
        }
        event = {
            "source": "r1:test",
            "time": datetime.fromisoformat("2026-07-01T00:00:00"),
            "lat": 34.0,
            "lon": -118.0,
            "depth": 8.0,
            "mag": 1.2,
            "picks": [pick_base],
        }
        duplicate = dict(event)
        duplicate["source"] = "r2:test"
        duplicate["picks"] = [dict(pick_base)]
        merged = merge_group([event, duplicate])
        pick = merged["picks"][0]
        self.assertEqual(pick["num_support"], 2)
        self.assertEqual(pick["sources"], "FT|SAR")
        self.assertEqual(
            pick["picker_cluster_sizes"], "SAR:4|FT:3|PHN:5|RUN:2"
        )
        self.assertAlmostEqual(pick["tp_std"], 0.2)
        self.assertEqual(pick["pick_provenance"], "both_groups")
        self.assertEqual(
            (pick["p_snr_e"], pick["p_snr_n"], pick["p_snr_z"]),
            (4.0, 6.0, 8.0),
        )

    def test_phase_merge_preserves_distinct_pairs_from_one_station(self):
        base = {
            "sta": "CI.ABC",
            "score": 1.0,
            "p_prob": 0.8,
            "s_prob": 0.9,
            "tp_std": 0.1,
            "ts_std": 0.1,
            "p_prob_std": 0.02,
            "s_prob_std": 0.02,
            "num_support": 4,
            "sources": "POS:SAR|POS:PHN",
            "picker_cluster_sizes": "POS:SAR:4|POS:PHN:3",
            "pick_provenance": "both_groups",
        }
        first = dict(base, p=datetime.fromisoformat("2026-07-01T00:00:01"),
                     s=datetime.fromisoformat("2026-07-01T00:00:04"))
        second = dict(base, p=datetime.fromisoformat("2026-07-01T00:00:08"),
                      s=datetime.fromisoformat("2026-07-01T00:00:13"))
        event = {
            "source": "test", "time": datetime.fromisoformat(
                "2026-07-01T00:00:00"
            ),
            "lat": 34.0, "lon": -118.0, "depth": 8.0, "mag": 1.2,
            "picks": [first, second],
        }
        merged = merge_group([event], phase_pick_tol=1.0)
        self.assertEqual(len(merged["picks"]), 2)


if __name__ == "__main__":
    unittest.main()
