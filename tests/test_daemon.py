import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from _load import ROOT

sys.path.insert(0, ROOT)
from trader.daemon import AsyncTradeDaemon, format_status, read_live_state


class TestTradeDaemon(unittest.TestCase):
    def test_daemon_square_off_lifecycle(self):
        daemon = AsyncTradeDaemon(
            symbol="INFY.NS",
            poll_interval=1,
            paper=True,
            simulated_time="2026-10-30 15:15",
        )
        asyncio.run(daemon.run_loop())
        self.assertEqual(daemon.state, "CLOSED")
        self.assertIn("15:15", daemon.exit_reason)
        self.assertGreater(daemon.realized_pnl, 0)

        # Check saved state
        st = read_live_state()
        self.assertIsNotNone(st)
        self.assertEqual(st["status"], "CLOSED")
        self.assertEqual(st["symbol"], "INFY.NS")

        # Test format_status
        out = format_status()
        self.assertIn("TRADE COMPLETED", out)
        self.assertIn("MIS_AUTO_SQUARE_OFF", out)


if __name__ == "__main__":
    unittest.main()
