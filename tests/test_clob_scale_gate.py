"""Scale gate evaluation (Supabase or CSV)."""
import tempfile
import unittest
from pathlib import Path

from scripts.clob_scale_gate import evaluate, rows_from_csv


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


if __name__ == "__main__":
    unittest.main()
