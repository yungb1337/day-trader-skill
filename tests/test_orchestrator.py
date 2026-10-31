import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from _load import ROOT

sys.path.insert(0, ROOT)
from trader.orchestrator import (
    _normalize_symbol,
    cmd_status,
    cmd_evaluate,
    cmd_plan,
    cmd_size,
    cmd_monitor,
    cmd_enter,
    cmd_squareoff,
)


class DummyArgs:
    def __init__(self, **kwargs):
        self.now = kwargs.get("now")
        self.offline = kwargs.get("offline")
        self.symbol = kwargs.get("symbol")
        self.setup = kwargs.get("setup", "orb")
        self.write = kwargs.get("write", False)
        self.target_r = kwargs.get("target_r", 1.5)
        self.flat_by = kwargs.get("flat_by")
        self.qty = kwargs.get("qty")
        self.price = kwargs.get("price")
        self.reason = kwargs.get("reason", "MANUAL_SQUARE_OFF")
        for k, v in kwargs.items():
            setattr(self, k, v)


class TestTraderOrchestrator(unittest.TestCase):
    def test_normalize_symbol(self):
        self.assertEqual(_normalize_symbol("infy"), "INFY.NS")
        self.assertEqual(_normalize_symbol("TCS.NS"), "TCS.NS")
        self.assertEqual(_normalize_symbol("RELIANCE.BO"), "RELIANCE.BO")
        self.assertIsNone(_normalize_symbol(None))

    def test_status_market_open(self):
        args = DummyArgs(now="2026-10-30 11:05")
        st = cmd_status(args)
        self.assertEqual(st["action"], "status")
        self.assertTrue(st["market_open"])
        self.assertEqual(st["phase"], "PRIME_SCANNING_WINDOW")
        self.assertEqual(st["time_str"], "11:05")

    def test_evaluate_breakout_trigger(self):
        args = DummyArgs(symbol="INFY.NS", now="2026-10-30 11:05", setup="orb", write=False)
        ev = cmd_evaluate(args)
        self.assertEqual(ev["action"], "evaluate")
        self.assertEqual(ev["symbol"], "INFY.NS")
        self.assertEqual(ev["status"], "triggered")
        self.assertGreater(ev["trigger"]["price"], 0)

    def test_plan_calculation(self):
        args = DummyArgs(now="2026-10-30 11:05", target_r=1.5, write=False)
        plan = cmd_plan(args)
        self.assertEqual(plan["action"], "plan")
        p = plan["plan"]
        self.assertEqual(p["symbol"], "INFY.NS")
        self.assertGreater(p["target"], p["entry"])
        self.assertLess(p["stop"], p["entry"])
        self.assertEqual(p["flat_by"], "15:15")

    def test_size_and_charges(self):
        args = DummyArgs(now="2026-10-30 11:05", write=False)
        sz = cmd_size(args)
        self.assertEqual(sz["action"], "size")
        self.assertGreater(sz["sizing"]["qty"], 0)
        self.assertGreater(sz["charges"]["total_charges"], 0)
        self.assertEqual(sz["order"]["product"], "MIS")

    def test_monitor_state_flow(self):
        # 1. Enter position
        enter_args = DummyArgs(symbol="INFY.NS", price=1170.85, qty=64, now="2026-10-30 11:05")
        res_enter = cmd_enter(enter_args)
        self.assertTrue(res_enter.get("filled"))

        # 2. Monitor during session
        mon_args = DummyArgs(now="2026-10-30 11:30")
        mon = cmd_monitor(mon_args)
        self.assertEqual(mon["status"], "IN_POSITION")
        self.assertEqual(mon["symbol"], "INFY.NS")
        self.assertEqual(mon["shares"], 64)

        # 3. Monitor at 15:15 squareoff
        mon_cutoff = DummyArgs(now="2026-10-30 15:15")
        mon_cut = cmd_monitor(mon_cutoff)
        self.assertEqual(mon_cut["signal"], "SQUARE_OFF_NOW")

        # 4. Squareoff
        sq_args = DummyArgs(price=1175.00, reason="MIS_AUTO_SQUARE_OFF", now="2026-10-30 15:15")
        sq = cmd_squareoff(sq_args)
        self.assertEqual(sq["action"], "squareoff")
        self.assertGreater(sq["net_pnl"], 0)

        # 5. Position now closed
        mon_closed = cmd_monitor(mon_args)
        self.assertEqual(mon_closed["status"], "NO_POSITION")


if __name__ == "__main__":
    unittest.main()
