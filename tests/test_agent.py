import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from _load import ROOT

sys.path.insert(0, ROOT)
from trader.agent import run_agent, render_trade_card


class TestTraderAgent(unittest.TestCase):
    def test_end_to_end_agent_pipeline(self):
        report = run_agent(
            symbol="INFY.NS",
            setup="orb",
            now="2026-10-30 11:05",
            config_path=os.path.join(ROOT, "trader", "config.json"),
        )
        self.assertEqual(report["status"], "READY")
        rec = report["recommendation"]
        self.assertIsNotNone(rec)

        # 1. What to buy assertions
        self.assertEqual(rec["symbol"], "INFY.NS")
        self.assertEqual(rec["tradingsymbol"], "INFY")
        self.assertEqual(rec["action"], "BUY")
        self.assertGreater(rec["shares"], 0)
        self.assertEqual(rec["product"], "MIS (Intraday)")

        # 2. Why behind it assertions
        self.assertIn("15-minute Opening Range Breakout", rec["technical_rationale"])
        self.assertIn("win_rate_pct", rec["statistical_edge"])
        self.assertGreater(rec["statistical_edge"]["win_rate_pct"], 50.0)
        self.assertGreaterEqual(rec["reward_risk"], 1.5)

        # 3. Target & Key levels assertions
        self.assertGreater(rec["target_price"], rec["entry_price"])
        self.assertLess(rec["stop_loss"], rec["entry_price"])
        self.assertEqual(rec["flat_by_ist"], "15:15")
        self.assertGreater(rec["charges_inr"], 0)
        self.assertGreater(rec["net_expected_gain_inr"], 0)

        # 4. Kite Connect payload assertions
        kite = rec["kite_order_payload"]
        self.assertEqual(kite["exchange"], "NSE")
        self.assertEqual(kite["tradingsymbol"], "INFY")
        self.assertEqual(kite["transaction_type"], "BUY")
        self.assertEqual(kite["product"], "MIS")

        # 5. Render card test
        card = render_trade_card(report)
        self.assertIn("AUTOMATED INTRADAY TRADE AGENT", card)
        self.assertIn("1. WHAT TO BUY", card)
        self.assertIn("2. THE \"WHY BEHIND IT\"", card)
        self.assertIn("3. TARGET & KEY PRICE LEVELS", card)
        self.assertIn("4. ONE-CLICK ZERODHA KITE CONNECT PAYLOAD", card)


if __name__ == "__main__":
    unittest.main()
