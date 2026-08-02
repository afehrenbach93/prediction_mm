"""Scale gate evaluation (Supabase or CSV)."""
import tempfile
import unittest
from pathlib import Path

from scripts.clob_scale_gate import evaluate, filter_live_actual, rows_from_csv


class TestScaleGate(unittest.TestCase):
    def test_pass(self):
        rows = [
            {"day": f"2026-07-{i:02d}", "net": 6, "est_gross": 10}
            for i in range(1, 15)
        ]
        code, msg = evaluate(rows, min_days=14, threshold=0.5)
        self.assertEqual(code, 0)
        self.assertIn("PASS", msg)

    def test_fail_ratio(self):
        rows = [
            {"day": f"2026-07-{i:02d}", "net": 2, "est_gross": 10}
            for i in range(1, 15)
        ]
        code, msg = evaluate(rows, min_days=14, threshold=0.5)
        self.assertEqual(code, 1)
        self.assertIn("FAIL", msg)

    def test_csv_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "pnl.csv"
            p.write_text(
                "day,net,est_gross\n"
                + "\n".join(f"2026-07-{i:02d},6,10" for i in range(1, 15))
                + "\n"
            )
            rows = rows_from_csv(p)
            self.assertEqual(len(rows), 14)
            code, _ = evaluate(rows, 14, 0.5)
            self.assertEqual(code, 0)

    def test_filter_live_actual(self):
        rows = [
            {"day": "2026-08-01", "note": "shadow_mtm", "net": 1, "est_gross": 10},
            {"day": "2026-08-02", "note": "live_actual", "net": 6, "est_gross": 10},
            {"day": "2026-08-03", "note": "live_stub", "net": 2, "est_gross": 10},
        ]
        live = filter_live_actual(rows)
        self.assertEqual(len(live), 1)
        self.assertEqual(live[0]["day"], "2026-08-02")


if __name__ == "__main__":
    unittest.main()
