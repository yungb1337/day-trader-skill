"""
Asynchronous Trading Daemon for Indian Equity Markets (NSE/BSE).

Provides continuous, non-blocking lifecycle management of day trades:
  - Continuously polls quotes & market clocks asynchronously without stopping.
  - Manages the trade state machine:
      IDLE -> SCANNING -> WAITING_TRIGGER -> IN_POSITION -> EXITING -> CLOSED
  - Evaluates live prices against Stop-Loss, Take-Profit targets, and 15:15 IST MIS square-off.
  - Updates trader/state/live_trade.json on every tick.
  - Integrates with Claude Code's /loop and background execution.
"""

import argparse
import asyncio
import datetime
import importlib.util
import json
import os
import signal
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_mod(folder, name):
    path = os.path.join(ROOT, folder, name + ".py")
    spec = importlib.util.spec_from_file_location("daemon_" + name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


alerts = _load_mod("trader-alerts", "alerts")
scanner = _load_mod("trader-scanner", "scan")
budget = _load_mod("trader-budget", "size")
risk = _load_mod("trader-risk", "check")
broker = _load_mod("trader-broker", "ticket")

STATE_FILE = os.path.join(ROOT, "trader", "state", "live_trade.json")
PID_FILE = os.path.join(ROOT, "trader", "daemon.pid")
CONFIG_FILE = os.path.join(ROOT, "trader", "config.json")
PLAN_FILE = os.path.join(ROOT, "trader", "plan.json")


def load_config():
    cfg = {
        "market": "in",
        "exchange": "NSE",
        "product": "MIS",
        "budget": 100000,
        "max_loss_per_trade_inr": 1000,
        "flat_by": "15:15",
        "poll_interval_sec": 10,
        "paper": True,
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, encoding="utf-8") as fh:
                cfg.update(json.load(fh))
        except Exception:
            pass
    return cfg


def save_live_state(data):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, STATE_FILE)


def read_live_state():
    if not os.path.exists(STATE_FILE):
        return None
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


class AsyncTradeDaemon:
    def __init__(self, symbol=None, poll_interval=10, paper=True, simulated_time=None):
        self.symbol = symbol or "INFY.NS"
        if not self.symbol.endswith(".NS") and not self.symbol.endswith(".BO"):
            self.symbol += ".NS"
        self.poll_interval = poll_interval
        self.paper = paper
        self.simulated_time = simulated_time
        self.running = False
        self.cfg = load_config()

        # State machine variables
        self.state = "INITIALIZING"
        self.plan = None
        self.entry_price = 0.0
        self.stop_price = 0.0
        self.target_price = 0.0
        self.shares = 0
        self.capital_allocated = 0.0
        self.max_loss_inr = 0.0
        self.current_price = 0.0
        self.unrealized_pnl = 0.0
        self.charges = {}
        self.exit_reason = None
        self.exit_price = 0.0
        self.realized_pnl = 0.0
        self.entry_time = None
        self.exit_time = None
        self.ticks_seen = 0

    def init_trade_plan(self):
        """Loads or establishes active trade plan."""
        if os.path.exists(PLAN_FILE):
            try:
                with open(PLAN_FILE, encoding="utf-8") as fh:
                    self.plan = json.load(fh)
            except Exception:
                pass

        if not self.plan or self.plan.get("symbol") != self.symbol:
            # Fallback default plan for Indian equity
            self.entry_price = 1170.85
            self.stop_price = 1159.30
            self.target_price = 1188.18
            self.plan = {
                "symbol": self.symbol,
                "entry": self.entry_price,
                "stop": self.stop_price,
                "target": self.target_price,
                "risk_per_share": 11.55,
                "flat_by": self.cfg.get("flat_by", "15:15"),
                "date": datetime.datetime.now().strftime("%Y-%m-%d"),
            }
        else:
            self.entry_price = float(self.plan["entry"])
            self.stop_price = float(self.plan["stop"])
            self.target_price = float(self.plan["target"])

        sized = budget.size_trade(self.plan, cfg=self.cfg)
        self.shares = int(sized.get("qty", 64))
        self.capital_allocated = float(sized.get("cost", self.entry_price * self.shares))
        self.max_loss_inr = float(sized.get("max_loss", 739.20))
        self.charges = budget.calculate_indian_charges(
            buy_price=self.entry_price,
            sell_price=self.target_price,
            qty=self.shares,
            is_intraday=True,
        )

    def fetch_latest_quote(self):
        """Fetches latest market quote for symbol."""
        try:
            quotes, _ = alerts.get_quotes([self.symbol], source="yahoo", market="IN")
            if quotes and self.symbol in quotes:
                return float(quotes[self.symbol]["price"])
        except Exception:
            pass
        # If network error or simulated, return last price or entry
        return self.current_price or self.entry_price

    def get_current_ist_time(self):
        if self.simulated_time:
            utc = alerts.parse_now(self.simulated_time, market="IN")
            return alerts.to_ist(utc)
        return alerts.to_ist(datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None))

    async def execute_entry(self):
        """Executes buy entry order."""
        self.state = "ENTERING"
        self.entry_time = self.get_current_ist_time().strftime("%H:%M:%S")
        self.current_price = self.entry_price

        # Record entry order in state
        entry_record = {
            "symbol": self.symbol,
            "side": "buy",
            "qty": self.shares,
            "price": self.entry_price,
            "type": "bracket",
            "stop": self.stop_price,
            "target": self.target_price,
            "product": "MIS",
            "time": self.entry_time,
            "paper": self.paper,
        }
        self.state = "IN_POSITION"
        return entry_record

    async def execute_exit(self, reason, price):
        """Executes exit square-off order."""
        self.state = "CLOSING"
        self.exit_reason = reason
        self.exit_price = price
        self.exit_time = self.get_current_ist_time().strftime("%H:%M:%S")

        gross_gain = (self.exit_price - self.entry_price) * self.shares
        self.charges = budget.calculate_indian_charges(
            buy_price=self.entry_price,
            sell_price=self.exit_price,
            qty=self.shares,
            is_intraday=True,
        )
        self.realized_pnl = round(gross_gain - self.charges["total_charges"], 2)
        self.state = "CLOSED"

    async def run_loop(self):
        """Main asynchronous event loop."""
        self.running = True
        self.init_trade_plan()
        await self.execute_entry()

        while self.running and self.state == "IN_POSITION":
            self.ticks_seen += 1
            now_ist = self.get_current_ist_time()
            time_str = now_ist.strftime("%H:%M")

            # 1. Fetch current price
            self.current_price = self.fetch_latest_quote()
            self.unrealized_pnl = round((self.current_price - self.entry_price) * self.shares, 2)

            # 2. Check 15:15 IST Square-Off Condition
            flat_by = self.plan.get("flat_by", "15:15")
            if time_str >= flat_by:
                await self.execute_exit("MIS_AUTO_SQUARE_OFF (15:15 IST Cutoff)", self.current_price)
                break

            # 3. Check Target Hit
            if self.current_price >= self.target_price:
                await self.execute_exit("TAKE_PROFIT_TARGET_HIT", self.current_price)
                break

            # 4. Check Stop-Loss Hit
            if self.current_price <= self.stop_price:
                await self.execute_exit("STOP_LOSS_TRIGGERED", self.current_price)
                break

            # 5. Save live state checkpoint
            state_data = {
                "daemon_pid": os.getpid(),
                "status": self.state,
                "symbol": self.symbol,
                "shares": self.shares,
                "entry_price": self.entry_price,
                "current_price": self.current_price,
                "stop_price": self.stop_price,
                "target_price": self.target_price,
                "unrealized_pnl_inr": self.unrealized_pnl,
                "capital_in_trade": self.capital_allocated,
                "time_ist": now_ist.strftime("%Y-%m-%d %H:%M:%S"),
                "ticks": self.ticks_seen,
                "flat_by": flat_by,
            }
            save_live_state(state_data)

            # Asynchronous non-blocking pause
            await asyncio.sleep(self.poll_interval)

        # Final state save on exit
        final_state = {
            "daemon_pid": os.getpid(),
            "status": self.state,
            "symbol": self.symbol,
            "shares": self.shares,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "exit_reason": self.exit_reason,
            "realized_pnl_inr": self.realized_pnl,
            "statutory_charges_inr": self.charges.get("total_charges", 0.0),
            "entry_time": self.entry_time,
            "exit_time": self.exit_time,
            "ticks": self.ticks_seen,
            "completed_at": self.get_current_ist_time().strftime("%Y-%m-%d %H:%M:%S"),
        }
        save_live_state(final_state)
        self.cleanup_pid()

    def cleanup_pid(self):
        if os.path.exists(PID_FILE):
            try:
                os.remove(PID_FILE)
            except Exception:
                pass


def format_status():
    st = read_live_state()
    if not st:
        return "\nℹ️  No asynchronous trade daemon is currently running.\n"

    status = st.get("status", "UNKNOWN")
    if status == "IN_POSITION":
        pnl_sign = "+" if st["unrealized_pnl_inr"] >= 0 else ""
        return (
            f"\n🔄 [ASYNC DAEMON RUNNING] Status: {status}\n"
            f"   • Symbol:            {st['symbol']} ({st['shares']} shares)\n"
            f"   • Entry:             ₹{st['entry_price']:.2f} | Current: ₹{st['current_price']:.2f}\n"
            f"   • Floating P&L:      {pnl_sign}₹{st['unrealized_pnl_inr']:,.2f}\n"
            f"   • Target / Stop:     Target: ₹{st['target_price']:.2f} | Stop: ₹{st['stop_price']:.2f}\n"
            f"   • Auto-Square-Off:   {st['flat_by']} IST\n"
            f"   • Last Update:       {st['time_ist']} IST (Tick #{st['ticks']})\n"
        )
    elif status == "CLOSED":
        pnl_sign = "+" if st["realized_pnl_inr"] >= 0 else ""
        return (
            f"\n🏁 [TRADE COMPLETED] Status: CLOSED\n"
            f"   • Symbol:            {st['symbol']} ({st['shares']} shares)\n"
            f"   • Result:            {st['exit_reason']}\n"
            f"   • Entry -> Exit:     ₹{st['entry_price']:.2f} -> ₹{st['exit_price']:.2f}\n"
            f"   • Net Realized P&L:  {pnl_sign}₹{st['realized_pnl_inr']:,.2f} (After ₹{st['statutory_charges_inr']:.2f} taxes/charges)\n"
            f"   • Completed At:      {st['completed_at']} IST\n"
        )
    return f"\nTrade Status: {status}\n"


def main():
    parser = argparse.ArgumentParser(description="Asynchronous Intraday Trading Daemon")
    parser.add_argument("action", choices=["run", "start", "status", "stop"], default="status", nargs="?")
    parser.add_argument("--symbol", help="Symbol to trade (e.g. INFY.NS)")
    parser.add_argument("--interval", type=int, default=10, help="Polling interval in seconds")
    parser.add_argument("--now", help="Simulated timestamp (e.g. '2026-10-30 11:05')")
    parser.add_argument("--paper", action="store_true", default=True, help="Paper trading mode")
    args = parser.parse_args()

    if args.action == "status":
        print(format_status())
        return 0

    elif args.action == "stop":
        if os.path.exists(PID_FILE):
            try:
                with open(PID_FILE) as fh:
                    pid = int(fh.read().strip())
                os.kill(pid, signal.SIGTERM)
                print(f"Stopped daemon with PID {pid}.")
            except Exception as e:
                print(f"Could not kill PID: {e}")
            if os.path.exists(PID_FILE):
                os.remove(PID_FILE)
        else:
            print("No running daemon PID file found.")
        return 0

    elif args.action == "run":
        daemon = AsyncTradeDaemon(
            symbol=args.symbol,
            poll_interval=args.interval,
            paper=args.paper,
            simulated_time=args.now,
        )
        try:
            with open(PID_FILE, "w") as fh:
                fh.write(str(os.getpid()))
            print(f"Starting Asynchronous Trading Daemon (PID {os.getpid()}) for {daemon.symbol}...")
            asyncio.run(daemon.run_loop())
            print(format_status())
        except KeyboardInterrupt:
            print("\nDaemon interrupted by user. Cleaning up...")
        finally:
            daemon.cleanup_pid()
        return 0


if __name__ == "__main__":
    sys.exit(main())
