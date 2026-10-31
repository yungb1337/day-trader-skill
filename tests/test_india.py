import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from _load import load

alerts = load("trader-alerts", "alerts")
scanner = load("trader-scanner", "scan")
entry = load("trader-entry", "entry")
exit_mod = load("trader-exit", "exit")
timemachine = load("trader-timemachine", "timemachine")
budget = load("trader-budget", "size")
risk = load("trader-risk", "check")
broker = load("trader-broker", "ticket")
news = load("trader-news", "news")
analyst = load("trader-analyst", "facts")
ptr = load("trader-pelosi", "ptr")


class TestIndiaAlerts(unittest.TestCase):
    def test_ist_conversions(self):
        # 09:15 IST is 03:45 UTC
        dt_utc = datetime.datetime(2026, 10, 19, 3, 45, tzinfo=datetime.timezone.utc)
        dt_ist = alerts.to_ist(dt_utc)
        self.assertEqual(dt_ist.hour, 9)
        self.assertEqual(dt_ist.minute, 15)

        # Naive IST to UTC
        naive_ist = datetime.datetime(2026, 10, 19, 9, 15)
        utc_back = alerts.ist_to_utc(naive_ist)
        self.assertEqual(utc_back.hour, 3)
        self.assertEqual(utc_back.minute, 45)

    def test_nse_trading_hours_and_holidays(self):
        # Regular trading day at 10:00 IST (04:30 UTC)
        dt_utc = datetime.datetime(2026, 10, 19, 4, 30, tzinfo=datetime.timezone.utc)
        st = alerts.market_status(dt_utc, market="IN")
        self.assertTrue(st["open"])
        self.assertEqual(st["opens"], "09:15")
        self.assertEqual(st["closes"], "15:30")

        # Before open at 08:30 IST (03:00 UTC)
        dt_pre = datetime.datetime(2026, 10, 19, 3, 0, tzinfo=datetime.timezone.utc)
        st_pre = alerts.market_status(dt_pre, market="IN")
        self.assertFalse(st_pre["open"])
        self.assertIn("before", st_pre["reason"])

        # After close at 16:00 IST (10:30 UTC)
        dt_post = datetime.datetime(2026, 10, 19, 10, 30, tzinfo=datetime.timezone.utc)
        st_post = alerts.market_status(dt_post, market="IN")
        self.assertFalse(st_post["open"])
        self.assertIn("after", st_post["reason"])

        # Republic Day (Jan 26) is an NSE holiday
        dt_holiday = datetime.datetime(2026, 1, 26, 5, 0, tzinfo=datetime.timezone.utc)
        st_hol = alerts.market_status(dt_holiday, market="IN")
        self.assertFalse(st_hol["open"])
        self.assertIn("Republic Day", st_hol["reason"])


class TestIndiaScanner(unittest.TestCase):
    def test_preserve_indian_symbol_dots(self):
        self.assertEqual(scanner.to_yahoo("RELIANCE.NS"), "RELIANCE.NS")
        self.assertEqual(scanner.to_yahoo("TCS.BO"), "TCS.BO")
        # US dual-class shares still converted
        self.assertEqual(scanner.to_yahoo("BRK.B"), "BRK-B")

    def test_nse_csv_universe_parser(self):
        csv_content = (
            "Company Name,Industry,Symbol,Series,ISIN Code\n"
            "Reliance Industries Ltd.,Oil & Gas,RELIANCE,EQ,INE002A01018\n"
            "Tata Motors Ltd.,Automobile,TATAMOTORS,EQ,INE155A01022\n"
            "Some Debt Paper,Debt,DEBTPAPER,BE,INE123A01010\n"
        )
        tickers, counts, _ = scanner.build_nse_universe(csv_content)
        syms = [t[0] for t in tickers]
        self.assertEqual(syms, ["RELIANCE.NS", "TATAMOTORS.NS"])
        self.assertEqual(counts["dropped_series"], 1)

    def test_indian_market_detection(self):
        self.assertTrue(scanner.is_indian_market(symbol="INFY.NS"))
        self.assertTrue(scanner.is_indian_market(symbol="HDFCBANK.BO"))
        self.assertTrue(scanner.is_indian_market(market="nse"))
        self.assertTrue(scanner.is_indian_market(config={"market": "in"}))
        self.assertFalse(scanner.is_indian_market(symbol="AAPL"))


class TestIndiaEntryExitTimeMachine(unittest.TestCase):
    def test_indian_defaults(self):
        self.assertEqual(entry.DEFAULTS_IN["no_entries_before"], "09:30")
        self.assertEqual(entry.DEFAULTS_IN["no_entries_after"], "15:00")
        self.assertEqual(exit_mod.DEFAULTS_IN["flat_by"], "15:15")

    def test_timemachine_market_params(self):
        us_params = timemachine.market_params(market="us")
        in_params = timemachine.market_params(market="in")
        self.assertEqual(us_params["session_bars"], 78)
        self.assertEqual(in_params["session_bars"], 75)
        self.assertEqual(in_params["open_min"], 555)  # 09:15
        self.assertEqual(in_params["close_min"], 930) # 15:30


class TestIndiaBudget(unittest.TestCase):
    def test_indian_statutory_charges(self):
        # 100 shares @ ₹1,000 = ₹1,00,000 buy; sell @ ₹1,050 = ₹1,05,000
        charges = budget.calculate_indian_charges(
            buy_price=1000.0, sell_price=1050.0, qty=100, is_intraday=True
        )
        self.assertGreater(charges["total_charges"], 0)
        self.assertIn("stt", charges)
        self.assertIn("nse_turnover", charges)
        self.assertIn("sebi_turnover", charges)
        self.assertIn("stamp_duty", charges)
        self.assertIn("gst", charges)
        self.assertIn("brokerage", charges)
        # Brokerage should be capped at ₹20 per executed leg (₹40 round trip)
        self.assertLessEqual(charges["brokerage"], 40.0)

    def test_kite_connect_order_generation(self):
        plan_item = {
            "symbol": "RELIANCE.NS",
            "market": "IN",
            "setup": "orb",
            "entry": 2500.0,
            "stop": 2480.0,
            "target": 2540.0,
            "risk_per_share": 20.0,
            "max_loss": 500.0,
            "qty": 25,
            "entry_time": "09:35",
            "flat_by": "15:15",
        }
        cfg = {"market": "in", "budget": 100000, "max_loss_per_trade_inr": 1000}
        sized = budget.size_trade(plan_item, cfg=cfg)
        order = budget.bracket_order(sized, cfg=cfg)
        self.assertEqual(order["exchange"], "NSE")
        self.assertEqual(order["tradingsymbol"], "RELIANCE")
        self.assertEqual(order["product"], "MIS")
        self.assertEqual(order["validity"], "DAY")
        self.assertIn("charges", order)


class TestIndiaRisk(unittest.TestCase):
    def test_pdt_rule_bypassed_for_india(self):
        # Even with < ₹25,000 and 4 day trades, Indian market should not fail PDT
        orders = [{
            "symbol": "INFY.NS",
            "side": "buy",
            "qty": 10,
            "type": "limit",
            "limit_price": 1800.0,
            "stop_loss_stop_price": 1780.0,
            "take_profit_limit_price": 1840.0,
            "max_loss": 200.0,
        }]
        cfg = {"market": "in", "budget": 50000, "max_loss_per_trade_inr": 500}
        # Simulate 4 day trades today
        fills = [
            {"date": "2026-10-19", "symbol": "TCS.NS", "side": "BUY", "qty": "5", "price": 3500.0},
            {"date": "2026-10-19", "symbol": "TCS.NS", "side": "SELL", "qty": "5", "price": 3550.0},
            {"date": "2026-10-19", "symbol": "WIPRO.NS", "side": "BUY", "qty": "10", "price": 500.0},
            {"date": "2026-10-19", "symbol": "WIPRO.NS", "side": "SELL", "qty": "10", "price": 510.0},
            {"date": "2026-10-19", "symbol": "INFY.NS", "side": "BUY", "qty": "10", "price": 1800.0},
            {"date": "2026-10-19", "symbol": "INFY.NS", "side": "SELL", "qty": "10", "price": 1820.0},
            {"date": "2026-10-19", "symbol": "SBIN.NS", "side": "BUY", "qty": "20", "price": 800.0},
            {"date": "2026-10-19", "symbol": "SBIN.NS", "side": "SELL", "qty": "20", "price": 805.0},
        ]
        state = {"orders": fills}
        # 10:00 IST is 04:30 UTC
        now_utc = datetime.datetime(2026, 10, 19, 4, 30)
        prices = {"INFY.NS": 1800.0}
        # Check should pass without raising PDT error
        res = risk.check(orders, cfg, state=state, prices=prices, today="2026-10-19", now=now_utc, market="in")
        self.assertEqual(res["block"], 0)
        self.assertEqual(res["pass"], 1)
        self.assertEqual(res["results"][0]["verdict"], "PASS")


class TestIndiaBroker(unittest.TestCase):
    def test_kite_tool_input(self):
        order = {
            "symbol": "TCS.NS",
            "side": "buy",
            "qty": 15,
            "type": "limit",
            "price": 3500.0,
            "trigger_price": 3480.0
        }
        ki = broker.kite_tool_input(order)
        self.assertEqual(ki["exchange"], "NSE")
        self.assertEqual(ki["tradingsymbol"], "TCS")
        self.assertEqual(ki["transaction_type"], "BUY")
        self.assertEqual(ki["quantity"], 15)
        self.assertEqual(ki["product"], "MIS")
        self.assertEqual(ki["order_type"], "LIMIT")
        self.assertEqual(ki["price"], 3500.0)
        self.assertEqual(ki["trigger_price"], 3480.0)


class TestIndiaNews(unittest.TestCase):
    def test_indian_keywords(self):
        items = [{
            "id": "1",
            "kind": "headline",
            "title": "SEBI issues show cause notice on promoter pledge",
            "summary": "Financial results delayed",
            "ticker": "RELIANCE.NS",
            "time": None
        }]
        tagged = news.tag(items, {"RELIANCE.NS": "Reliance"})
        self.assertIn("legal", tagged[0]["tags"])
        self.assertIn("regulatory", tagged[0]["tags"])
        self.assertIn("people", tagged[0]["tags"])
        self.assertIn("risk", tagged[0]["tags"])


class TestIndiaAnalyst(unittest.TestCase):
    def test_inr_formatting(self):
        self.assertEqual(analyst.format_inr(50000000), "₹5.00 Cr")
        self.assertEqual(analyst.format_inr(250000), "₹2.50 L")
        self.assertEqual(analyst.format_inr(1500), "₹1,500")


class TestIndiaPelosiSuperInvestors(unittest.TestCase):
    def test_super_investor_matching(self):
        self.assertEqual(ptr.match_super_investor("RADHAKISHAN SHIVKISHAN DAMANI"), "damani")
        self.assertEqual(ptr.match_super_investor("RARE ENTERPRISES"), "jhunjhunwala")
        self.assertEqual(ptr.match_super_investor("VIJAY KEDIAS"), "kedia")
        self.assertEqual(ptr.match_super_investor("ASHISH RAMCHANDRA KACHOLIA"), "kacholia")

    def test_parse_indian_deal(self):
        raw = {
            "date": "2026-10-15",
            "symbol": "TATAMOTORS",
            "securityName": "Tata Motors Ltd.",
            "clientName": "RADHAKISHAN DAMANI",
            "dealType": "BUY",
            "quantity": "500,000",
            "tradePrice": "980.50"
        }
        deal = ptr.parse_indian_deal(raw)
        self.assertEqual(deal["symbol"], "TATAMOTORS.NS")
        self.assertEqual(deal["deal_type"], "BUY")
        self.assertEqual(deal["quantity"], 500000)
        self.assertEqual(deal["trade_price"], 980.5)
        self.assertEqual(deal["value_cr"], 49.02)
        self.assertEqual(deal["super_investor"], "damani")


if __name__ == "__main__":
    unittest.main()
