"""
Orchestrator CLI for the Persistent Trading Agent.

Claude Code stays alive as the reasoning brain. This script provides
the deterministic Python tools that Claude calls each tick via /loop.

Architecture:
  Claude Code (/loop) --> orchestrator.py <action> --> returns structured data
  Claude reads the output, REASONS about it, decides the next action.

Actions (each is one CLI call, stateless, returns JSON):
  status     - market clock, session phase, any open position
  scan       - run ORB or daily scanner, return candidates
  news       - check headlines for a symbol
  evaluate   - check if an entry has triggered for a symbol
  plan       - build exit plan (stop, target, R:R) from a triggered entry
  simulate   - run Time Machine Monte Carlo replay
  size       - size the trade, calculate statutory charges
  riskcheck  - run risk gatekeeper on a proposed order
  quote      - fetch latest price for a symbol
  recommend  - full pipeline in one shot (scan -> ... -> risk check)
  enter      - record a paper entry fill
  monitor    - check open position against stop/target/15:15 square-off
  squareoff  - generate and record the closing sell order
  log        - show today's trade decisions

State lives in trader/*.json files. Every action reads what it needs
and writes what it produces. Claude Code orchestrates the sequence.
"""

import argparse
import datetime
import importlib.util
import json
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRADER_DIR = os.path.join(ROOT, "trader")
STATE_DIR = os.path.join(TRADER_DIR, "state")


def _load_mod(folder, name):
    path = os.path.join(ROOT, folder, name + ".py")
    spec = importlib.util.spec_from_file_location("orch_" + name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Lazy-load modules on first use to keep startup fast
_mods = {}

def _mod(folder, name):
    key = folder + "/" + name
    if key not in _mods:
        _mods[key] = _load_mod(folder, name)
    return _mods[key]


def _read_json(path, default=None):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, path)


def _cfg():
    return _read_json(os.path.join(TRADER_DIR, "config.json"), {})


def _now_utc(now_str=None):
    alerts = _mod("trader-alerts", "alerts")
    if now_str:
        return alerts.parse_now(now_str, market="IN")
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def _now_ist(now_str=None):
    alerts = _mod("trader-alerts", "alerts")
    return alerts.to_ist(_now_utc(now_str))


# ---------------------------------------------------------------- actions

def cmd_status(args):
    """Market clock + session phase + open position summary."""
    alerts = _mod("trader-alerts", "alerts")
    utc = _now_utc(args.now)
    ist = alerts.to_ist(utc)
    st = alerts.market_status(utc, market="IN")

    # Check for open position
    portfolio = _read_json(os.path.join(TRADER_DIR, "portfolio.json"), {})
    positions = portfolio.get("positions", [])
    plan = _read_json(os.path.join(TRADER_DIR, "plan.json"))
    entry = _read_json(os.path.join(TRADER_DIR, "entry.json"))
    live = _read_json(os.path.join(STATE_DIR, "live_trade.json"))

    # Determine session phase
    time_str = ist.strftime("%H:%M")
    if not st.get("open"):
        phase = "CLOSED"
    elif time_str < "09:15":
        phase = "PRE_MARKET"
    elif time_str < "09:30":
        phase = "OPENING_RANGE_FORMING"
    elif time_str < "09:35":
        phase = "OPENING_RANGE_COMPLETE"
    elif time_str < "13:00":
        phase = "PRIME_SCANNING_WINDOW"
    elif time_str < "15:00":
        phase = "AFTERNOON_SESSION"
    elif time_str < "15:15":
        phase = "SQUARE_OFF_WINDOW"
    else:
        phase = "POST_SQUARE_OFF"

    result = {
        "action": "status",
        "time_ist": ist.strftime("%Y-%m-%d %H:%M:%S"),
        "time_str": time_str,
        "date": ist.date().isoformat(),
        "weekday": ist.strftime("%A"),
        "market_open": st.get("open", False),
        "reason": st.get("reason", ""),
        "phase": phase,
        "session": {"opens": st.get("opens"), "closes": st.get("closes")} if st.get("opens") else None,
        "next_open": st.get("next_open"),
        "open_positions": len(positions),
        "position_symbols": [p.get("ticker", p.get("symbol")) for p in positions],
        "has_plan": plan is not None and plan.get("date") == ist.date().isoformat(),
        "has_entry": entry is not None and entry.get("date") == ist.date().isoformat(),
        "live_trade": live.get("status") if live else None,
        "cash": portfolio.get("cash"),
    }
    return result


def cmd_scan(args):
    """Run ORB or daily scanner, return candidates."""
    scanner = _mod("trader-scanner", "scan")
    alerts = _mod("trader-alerts", "alerts")
    utc = _now_utc(args.now)
    ist = alerts.to_ist(utc)
    cfg = _cfg()

    setup = args.setup or "orb"
    result = {"action": "scan", "setup": setup, "time_ist": ist.strftime("%H:%M"), "candidates": [], "errors": []}

    if setup == "orb":
        at_time = ist.time() if not args.at else datetime.datetime.strptime(args.at, "%H:%M").time()
        or_minutes = int(args.or_minutes or 15)
        until = None
        nea = cfg.get("no_entries_after")
        if nea:
            try:
                until = datetime.datetime.strptime(str(nea), "%H:%M").time()
            except Exception:
                pass

        try:
            day, cutoff = scanner.orb_window(utc, at_time, or_minutes, until, market="IN")
        except scanner.ScanError as e:
            result["errors"].append(str(e))
            return result

        # Load symbols from watchlist or default liquid stocks
        symbols = _load_scan_symbols(args)

        # Fetch charts and scan
        stage1, failed = {}, {}
        if args.offline:
            try:
                s, _ = scanner.load_offline(args.offline)
                stage1.update(s)
            except Exception as e:
                failed["offline"] = str(e)
        else:
            rng = "1d" if day == ist.date() else "5d"
            try:
                stage1, spark_failed = scanner.fetch_spark(symbols, rng, "5m", 0.0)
                failed.update(spark_failed)
            except Exception as e:
                failed["spark"] = str(e)

        hits, counts, scan_failed = scanner.scan_orb(
            stage1, symbols, day, cutoff, or_minutes, market="IN"
        )
        failed.update(scan_failed)

        result["date"] = day.isoformat()
        result["candidates"] = hits
        result["counts"] = counts
        result["failed"] = failed
        result["universe_size"] = len(symbols)

    return result


def cmd_news(args):
    """Check headlines for a symbol."""
    news = _mod("trader-news", "news")
    utc = _now_utc(args.now)
    now_aware = utc.replace(tzinfo=datetime.timezone.utc)
    sym = _normalize_symbol(args.symbol)

    result = {"action": "news", "symbol": sym, "headlines": [], "errors": []}

    try:
        fetcher = news.Fetcher(offline=args.offline)
        watch = {sym: None}
        items = news.collect(fetcher, watch, state={}, sec=False, market="IN")
        tagged = news.tag(items, watch)
        selected = news.select(tagged, state={}, now=now_aware, hours=int(args.hours or 24), show_all=True)

        result["headlines"] = [
            {"title": it.get("title", ""), "source": it.get("source", ""),
             "tags": it.get("tags", []), "time": it.get("time", ""),
             "link": it.get("link", "")}
            for it in selected[:10]
        ]
        result["total_found"] = len(selected)
    except Exception as e:
        result["errors"].append(str(e))

    return result


def cmd_evaluate(args):
    """Check if an entry has triggered for a symbol."""
    entry_mod = _mod("trader-entry", "entry")
    utc = _now_utc(args.now)
    sym = _normalize_symbol(args.symbol)

    result = {"action": "evaluate", "symbol": sym, "errors": []}

    try:
        chart = entry_mod.get_chart(sym, "5m", offline=args.offline)
        res = entry_mod.evaluate(chart, utc, setup=args.setup or "orb", market="IN")
        pub = entry_mod.public(res)
        result.update(pub)

        # Write entry.json if triggered
        if res.get("status") == "triggered" and args.write:
            doc = entry_mod.entry_doc(res, utc)
            _write_json(os.path.join(TRADER_DIR, "entry.json"), doc)
            result["wrote"] = "trader/entry.json"
    except Exception as e:
        result["errors"].append(str(e))
        result["status"] = "error"

    return result


def cmd_plan(args):
    """Build exit plan from a triggered entry."""
    exit_mod = _mod("trader-exit", "exit")
    utc = _now_utc(args.now)
    ist = _mod("trader-alerts", "alerts").to_ist(utc)
    cfg = _cfg()

    result = {"action": "plan", "errors": []}

    entry_doc = _read_json(os.path.join(TRADER_DIR, "entry.json"))
    if not entry_doc or entry_doc.get("status") != "triggered":
        result["errors"].append("No triggered entry in trader/entry.json. Run 'evaluate' first.")
        return result

    sym = entry_doc["symbol"]
    entry_price = float(entry_doc["price"])
    or_data = entry_doc.get("opening_range", {})
    or_high = float(or_data.get("high", entry_price))
    or_low = float(or_data.get("low", entry_price * 0.98))

    try:
        target_rule, target_param = exit_mod.parse_target("%sR" % (args.target_r or 1.5))
        comp = exit_mod.compute(
            entry=entry_price,
            stop_rule="or_mid",
            stop_param=None,
            target_rule=target_rule,
            target_param=target_param,
            or_high=or_high,
            or_low=or_low,
        )
        flat_by = args.flat_by or cfg.get("flat_by", "15:15")

        plan_doc = {
            "created_by": "trader-orchestrator",
            "symbol": sym,
            "side": entry_doc.get("side", "buy"),
            "date": entry_doc.get("date", ist.date().isoformat()),
            "setup": entry_doc.get("setup", "orb"),
            "entry": float(comp["entry"]),
            "entry_time": entry_doc.get("time", ist.strftime("%I:%M %p")),
            "entry_bar": entry_doc.get("bar_index"),
            "interval": entry_doc.get("interval", "5m"),
            "opening_range": or_data,
            "stop": float(comp["stop"]),
            "stop_rule": "or_mid",
            "stop_label": str(comp.get("stop_label", "opening-range midpoint")),
            "target": float(comp["target"]),
            "target_r": float(comp.get("target_r", 1.5)),
            "target_label": str(comp.get("target_label", "")),
            "risk_per_share": float(comp["risk"]),
            "reward_risk": float(comp["reward_risk"]),
            "flat_by": flat_by,
            "stop_detail": str(comp.get("stop_detail", "")),
            "created_at": utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "note": "A stop is a plan, not a guarantee: a gap or a fast market can fill past it.",
            "market": "IN",
            "currency": "INR",
        }

        if args.write:
            _write_json(os.path.join(TRADER_DIR, "plan.json"), plan_doc)
            result["wrote"] = "trader/plan.json"

        result["plan"] = plan_doc
        result["description"] = exit_mod.describe(plan_doc)
    except Exception as e:
        result["errors"].append(str(e))

    return result


def cmd_simulate(args):
    """Run Time Machine Monte Carlo replay."""
    tm = _mod("trader-timemachine", "timemachine")
    utc = _now_utc(args.now)

    result = {"action": "simulate", "errors": []}

    plan = _read_json(os.path.join(TRADER_DIR, "plan.json"))
    if not plan:
        result["errors"].append("No plan in trader/plan.json. Run 'plan' first.")
        return result

    sym = plan["symbol"]
    try:
        if args.offline:
            raw = tm.read_offline(args.offline, sym)
        else:
            raw = tm.fetch_chart(sym)

        doc = tm.time_machine(raw, plan, utc, replays=int(args.replays or 10000), source="yahoo")

        if args.write:
            _write_json(os.path.join(TRADER_DIR, "timemachine.json"), doc)
            result["wrote"] = "trader/timemachine.json"

        mc = doc.get("mc", {})
        result["symbol"] = sym
        result["replays"] = mc.get("replays", 0)
        result["win_rate_pct"] = round((mc.get("p_target", 0) + mc.get("p_green", 0)) / 2, 1) if mc else 0
        result["p_target"] = mc.get("p_target")
        result["p_stop"] = mc.get("p_stop")
        result["p_neither"] = mc.get("p_neither")
        result["avg_R"] = mc.get("avg_R")
        result["verdict"] = doc.get("verdict", "")
        result["chart"] = tm.render_chart(doc) if doc.get("mc") else []
    except Exception as e:
        result["errors"].append(str(e))

    return result


def cmd_size(args):
    """Size the trade and calculate statutory charges."""
    budget_mod = _mod("trader-budget", "size")
    ist = _now_ist(args.now)
    cfg = _cfg()

    result = {"action": "size", "errors": []}

    plan = _read_json(os.path.join(TRADER_DIR, "plan.json"))
    if not plan:
        result["errors"].append("No plan in trader/plan.json. Run 'plan' first.")
        return result

    try:
        sized = budget_mod.size_trade(plan, cfg=cfg, today=ist.date().isoformat())
        order = budget_mod.bracket_order(sized, cfg=cfg)

        if args.write and order:
            doc = {
                "created_by": "trader-orchestrator",
                "created_at": ist.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "status": "proposed",
                "paper": bool(cfg.get("paper", True)),
                "note": "Nothing is placed until trader-risk passes it and you say yes.",
                "orders": [order],
            }
            _write_json(os.path.join(TRADER_DIR, "orders.json"), doc)
            result["wrote"] = "trader/orders.json"

        result["sizing"] = {
            "symbol": sized.get("symbol"),
            "qty": sized.get("qty"),
            "entry": sized.get("entry"),
            "stop": sized.get("stop"),
            "target": sized.get("target"),
            "cost": sized.get("cost"),
            "max_loss": sized.get("max_loss"),
            "bound_by": sized.get("bound_by"),
            "risk_per_share": sized.get("risk_per_share"),
            "reward_risk": sized.get("reward_risk"),
        }
        if order and order.get("charges"):
            result["charges"] = order["charges"]
        result["order"] = order
        result["report"] = budget_mod.plan_report(sized, order)
    except Exception as e:
        result["errors"].append(str(e))

    return result


def cmd_riskcheck(args):
    """Run risk gatekeeper on proposed orders."""
    risk_mod = _mod("trader-risk", "check")
    utc = _now_utc(args.now)
    ist = _mod("trader-alerts", "alerts").to_ist(utc)
    cfg = _cfg()

    result = {"action": "riskcheck", "errors": []}

    orders_doc = _read_json(os.path.join(TRADER_DIR, "orders.json"))
    if not orders_doc:
        result["errors"].append("No orders in trader/orders.json. Run 'size' first.")
        return result

    orders = orders_doc.get("orders", orders_doc) if isinstance(orders_doc, dict) else orders_doc
    portfolio = _read_json(os.path.join(TRADER_DIR, "portfolio.json"), {})
    state = _read_json(os.path.join(STATE_DIR, "broker.json"), {})

    try:
        # Get prices for position valuation
        prices = {}
        for o in orders:
            sym = o.get("symbol", "")
            prices[sym] = float(o.get("limit_price", o.get("entry", o.get("price", 0))))

        res = risk_mod.check(
            orders, cfg, portfolio=portfolio, prices=prices,
            state=state, today=ist.date().isoformat(), now=utc, market="in"
        )

        if args.write:
            _write_json(os.path.join(STATE_DIR, "risk.json"), res)
            result["wrote"] = "trader/state/risk.json"

        result["passed"] = res.get("pass", 0)
        result["blocked"] = res.get("block", 0)
        result["results"] = res.get("results", [])
        result["warnings"] = res.get("warnings", [])
        result["verdict"] = "ALL_PASSED" if res.get("block", 0) == 0 else "BLOCKED"
        result["report"] = risk_mod.report(res)
    except Exception as e:
        result["errors"].append(str(e))

    return result


def cmd_quote(args):
    """Fetch latest price for a symbol."""
    alerts = _mod("trader-alerts", "alerts")
    sym = _normalize_symbol(args.symbol)

    result = {"action": "quote", "symbol": sym, "errors": []}

    try:
        quotes, errors = alerts.get_quotes([sym], source="yahoo", market="IN")
        if sym in quotes:
            q = quotes[sym]
            result["price"] = q.get("price")
            result["prev_close"] = q.get("prev_close")
            result["change_pct"] = round(100.0 * (q["price"] / q["prev_close"] - 1), 2) if q.get("prev_close") else None
            result["time"] = q.get("time")
            result["currency"] = q.get("currency", "INR")
        if errors:
            result["errors"].extend(errors)
    except Exception as e:
        result["errors"].append(str(e))

    return result


def cmd_monitor(args):
    """Check open position against stop, target, and 15:15 IST square-off."""
    alerts = _mod("trader-alerts", "alerts")
    utc = _now_utc(args.now)
    ist = alerts.to_ist(utc)
    time_str = ist.strftime("%H:%M")

    result = {"action": "monitor", "time_ist": ist.strftime("%H:%M:%S"), "errors": []}

    portfolio = _read_json(os.path.join(TRADER_DIR, "portfolio.json"), {})
    positions = portfolio.get("positions", [])
    if not positions:
        result["status"] = "NO_POSITION"
        result["message"] = "No open positions to monitor."
        return result

    plan = _read_json(os.path.join(TRADER_DIR, "plan.json"), {})
    flat_by = plan.get("flat_by", _cfg().get("flat_by", "15:15"))

    pos = positions[0]
    sym = pos.get("ticker", pos.get("symbol", ""))
    shares = int(pos.get("shares", pos.get("qty", 0)))
    entry_cost = float(pos.get("lots", [{}])[0].get("cost", 0))
    entry_price = round(entry_cost / shares, 2) if shares > 0 else 0

    # Fetch live quote
    try:
        quotes, _ = alerts.get_quotes([sym], source="yahoo", market="IN")
        current_price = float(quotes[sym]["price"]) if sym in quotes else entry_price
    except Exception:
        current_price = entry_price

    stop = float(plan.get("stop", 0))
    target = float(plan.get("target", 0))
    unrealized = round((current_price - entry_price) * shares, 2)

    # Determine trade status
    signal = "HOLD"
    if time_str >= flat_by:
        signal = "SQUARE_OFF_NOW"
        result["reason"] = "15:15 IST MIS auto-square-off deadline reached"
    elif stop > 0 and current_price <= stop:
        signal = "STOP_HIT"
        result["reason"] = "Price hit stop-loss at %.2f" % stop
    elif target > 0 and current_price >= target:
        signal = "TARGET_HIT"
        result["reason"] = "Price reached take-profit target at %.2f" % target

    result.update({
        "status": "IN_POSITION",
        "signal": signal,
        "symbol": sym,
        "shares": shares,
        "entry_price": entry_price,
        "current_price": current_price,
        "stop": stop,
        "target": target,
        "flat_by": flat_by,
        "unrealized_pnl": unrealized,
        "pnl_pct": round(unrealized / entry_cost * 100, 2) if entry_cost > 0 else 0,
    })
    return result


def cmd_enter(args):
    """Record a paper entry fill."""
    broker_mod = _mod("trader-broker", "ticket")
    ist = _now_ist(args.now)
    sym = _normalize_symbol(args.symbol)

    result = {"action": "enter", "symbol": sym, "errors": []}

    plan = _read_json(os.path.join(TRADER_DIR, "plan.json"), {})
    qty = int(args.qty or plan.get("qty") or 64)
    price = float(args.price or plan.get("entry", 0))

    if price <= 0:
        result["errors"].append("No entry price. Provide --price or ensure trader/plan.json exists.")
        return result

    # Update portfolio
    portfolio = _read_json(os.path.join(TRADER_DIR, "portfolio.json"), {"cash": 100000, "positions": []})
    cost = round(price * qty, 2)
    portfolio["cash"] = round(portfolio["cash"] - cost, 2)
    portfolio["positions"].append({
        "ticker": sym,
        "shares": float(qty),
        "lots": [{"id": "%s-1" % sym, "date": ist.date().isoformat(), "shares": float(qty), "cost": cost}],
    })
    _write_json(os.path.join(TRADER_DIR, "portfolio.json"), portfolio)

    # Record in broker state
    state = _read_json(os.path.join(STATE_DIR, "broker.json"), {"orders": []})
    state.setdefault("orders", []).append({
        "at": ist.strftime("%Y-%m-%dT%H:%M:%S+05:30"),
        "date": ist.date().isoformat(),
        "symbol": sym,
        "side": "buy",
        "status": "placed",
        "plain": "BUY %d %s at %.2f (paper)" % (qty, sym, price),
    })
    state["orders"].append({
        "at": ist.strftime("%Y-%m-%dT%H:%M:%S+05:30"),
        "date": ist.date().isoformat(),
        "symbol": sym,
        "side": "buy",
        "status": "filled",
        "plain": "BUY %d %s at %.2f" % (qty, sym, price),
    })
    _write_json(os.path.join(STATE_DIR, "broker.json"), state)

    # Save live trade state
    live = {
        "status": "IN_POSITION",
        "symbol": sym,
        "shares": qty,
        "entry_price": price,
        "entry_time": ist.strftime("%H:%M:%S"),
        "stop_price": float(plan.get("stop", 0)),
        "target_price": float(plan.get("target", 0)),
        "flat_by": plan.get("flat_by", "15:15"),
        "paper": True,
    }
    _write_json(os.path.join(STATE_DIR, "live_trade.json"), live)

    result.update({
        "filled": True,
        "qty": qty,
        "price": price,
        "cost": cost,
        "cash_remaining": portfolio["cash"],
    })
    return result


def cmd_squareoff(args):
    """Generate and record the closing sell order."""
    budget_mod = _mod("trader-budget", "size")
    ist = _now_ist(args.now)

    result = {"action": "squareoff", "errors": []}

    portfolio = _read_json(os.path.join(TRADER_DIR, "portfolio.json"), {})
    positions = portfolio.get("positions", [])
    if not positions:
        result["errors"].append("No open positions to square off.")
        return result

    pos = positions[0]
    sym = pos.get("ticker", pos.get("symbol", ""))
    shares = int(pos.get("shares", 0))
    entry_cost = float(pos.get("lots", [{}])[0].get("cost", 0))
    entry_price = round(entry_cost / shares, 2) if shares > 0 else 0

    exit_price = float(args.price or 0)
    if exit_price <= 0:
        # Fetch current price
        try:
            alerts = _mod("trader-alerts", "alerts")
            quotes, _ = alerts.get_quotes([sym], source="yahoo", market="IN")
            exit_price = float(quotes[sym]["price"]) if sym in quotes else entry_price
        except Exception:
            exit_price = entry_price

    # Calculate P&L with statutory charges
    charges = budget_mod.calculate_indian_charges(
        buy_price=entry_price, sell_price=exit_price, qty=shares, is_intraday=True
    )
    gross_pnl = round((exit_price - entry_price) * shares, 2)
    net_pnl = round(gross_pnl - charges["total_charges"], 2)

    # Update portfolio: remove position, add cash back
    proceeds = round(exit_price * shares, 2)
    portfolio["cash"] = round(portfolio["cash"] + proceeds, 2)
    portfolio["positions"] = []
    _write_json(os.path.join(TRADER_DIR, "portfolio.json"), portfolio)

    # Record in broker state
    state = _read_json(os.path.join(STATE_DIR, "broker.json"), {"orders": []})
    state.setdefault("orders", []).append({
        "at": ist.strftime("%Y-%m-%dT%H:%M:%S+05:30"),
        "date": ist.date().isoformat(),
        "symbol": sym,
        "side": "sell",
        "status": "placed",
        "plain": "SELL %d %s at %.2f (square-off)" % (shares, sym, exit_price),
    })
    state["orders"].append({
        "at": ist.strftime("%Y-%m-%dT%H:%M:%S+05:30"),
        "date": ist.date().isoformat(),
        "symbol": sym,
        "side": "sell",
        "status": "filled",
        "plain": "SELL %d %s at %.2f" % (shares, sym, exit_price),
    })
    _write_json(os.path.join(STATE_DIR, "broker.json"), state)

    # Update live trade state
    live = _read_json(os.path.join(STATE_DIR, "live_trade.json"), {})
    live.update({
        "status": "CLOSED",
        "exit_price": exit_price,
        "exit_time": ist.strftime("%H:%M:%S"),
        "exit_reason": args.reason or "MANUAL_SQUARE_OFF",
        "gross_pnl": gross_pnl,
        "charges": charges["total_charges"],
        "net_pnl": net_pnl,
    })
    _write_json(os.path.join(STATE_DIR, "live_trade.json"), live)

    result.update({
        "symbol": sym,
        "shares": shares,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "gross_pnl": gross_pnl,
        "charges": charges,
        "net_pnl": net_pnl,
        "cash_after": portfolio["cash"],
    })
    return result


def cmd_recommend(args):
    """Full pipeline in one shot: scan -> entry -> plan -> simulate -> size -> risk."""
    from trader.agent import run_agent, render_trade_card
    report = run_agent(
        symbol=args.symbol,
        setup=args.setup or "orb",
        now=args.now,
        config_path=os.path.join(TRADER_DIR, "config.json"),
    )
    return report


def cmd_log(args):
    """Show today's trade decisions."""
    ist = _now_ist(args.now)
    state = _read_json(os.path.join(STATE_DIR, "broker.json"), {})
    today = ist.date().isoformat()
    rows = [r for r in state.get("orders", []) if r.get("date") == today]
    return {"action": "log", "date": today, "decisions": rows, "count": len(rows)}


# ---------------------------------------------------------------- helpers

def _normalize_symbol(sym):
    if not sym:
        return None
    sym = sym.strip().upper()
    if not sym.endswith(".NS") and not sym.endswith(".BO"):
        sym += ".NS"
    return sym


def _load_scan_symbols(args):
    """Load watchlist symbols or default liquid Indian stocks."""
    wl = _read_json(os.path.join(TRADER_DIR, "watchlist.json"))
    if wl:
        syms = [s.get("symbol", s) if isinstance(s, dict) else s for s in wl.get("watchlist", wl.get("symbols", []))]
        if syms:
            return [_normalize_symbol(s) for s in syms if s]

    return [
        "RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS", "ICICIBANK.NS",
        "SBIN.NS", "BHARTIARTL.NS", "ITC.NS", "KOTAKBANK.NS", "LT.NS",
        "AXISBANK.NS", "WIPRO.NS", "HCLTECH.NS", "MARUTI.NS", "TATASTEEL.NS",
        "SUNPHARMA.NS", "BAJFINANCE.NS", "NESTLEIND.NS", "TITAN.NS", "ULTRACEMCO.NS",
    ]


# ---------------------------------------------------------------- CLI

def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Orchestrator CLI for the Persistent Trading Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="cmd")

    def add_common(p):
        p.add_argument("--now", help="Simulated time (e.g. '2026-10-30 11:05')")
        p.add_argument("--offline", help="Offline data directory")

    p = sub.add_parser("status", help="Market clock + session phase + open position")
    add_common(p)

    p = sub.add_parser("scan", help="Run ORB or daily scanner")
    add_common(p)
    p.add_argument("--setup", default="orb", choices=["orb", "daily"])
    p.add_argument("--at", help="Cutoff time (HH:MM)")
    p.add_argument("--or-minutes", type=int, default=15)

    def add_symbol(parser):
        parser.add_argument("symbol_pos", nargs="?", default=None, help="Ticker (e.g. INFY.NS)")
        parser.add_argument("--symbol", dest="symbol_flag", default=None, help="Ticker (e.g. INFY.NS)")

    p = sub.add_parser("news", help="Check headlines for a symbol")
    add_common(p)
    add_symbol(p)
    p.add_argument("--hours", type=int, default=24)

    p = sub.add_parser("evaluate", help="Check entry trigger for a symbol")
    add_common(p)
    add_symbol(p)
    p.add_argument("--setup", default="orb")
    p.add_argument("--write", action="store_true")

    p = sub.add_parser("plan", help="Build exit plan from triggered entry")
    add_common(p)
    add_symbol(p)
    p.add_argument("--target-r", type=float, default=1.5)
    p.add_argument("--flat-by", default=None)
    p.add_argument("--write", action="store_true")

    p = sub.add_parser("simulate", help="Run Time Machine Monte Carlo")
    add_common(p)
    p.add_argument("--replays", type=int, default=10000)
    p.add_argument("--write", action="store_true")

    p = sub.add_parser("size", help="Size trade and calculate charges")
    add_common(p)
    p.add_argument("--write", action="store_true")

    p = sub.add_parser("riskcheck", help="Run risk gatekeeper")
    add_common(p)
    p.add_argument("--write", action="store_true", default=True)

    p = sub.add_parser("quote", help="Fetch latest price for a symbol")
    add_common(p)
    add_symbol(p)

    p = sub.add_parser("monitor", help="Check open position status")
    add_common(p)

    p = sub.add_parser("enter", help="Record a paper entry fill")
    add_common(p)
    add_symbol(p)
    p.add_argument("--qty", type=int)
    p.add_argument("--price", type=float)

    p = sub.add_parser("squareoff", help="Close an open position")
    add_common(p)
    p.add_argument("--price", type=float, help="Exit price (default: fetch live)")
    p.add_argument("--reason", default="MANUAL_SQUARE_OFF")

    p = sub.add_parser("recommend", help="Full pipeline in one shot")
    add_common(p)
    p.add_argument("--symbol")
    p.add_argument("--setup", default="orb")

    p = sub.add_parser("log", help="Today's trade decisions")
    add_common(p)

    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        return 1

    resolved_sym = getattr(args, "symbol_flag", None) or getattr(args, "symbol_pos", None) or getattr(args, "symbol", None)
    args.symbol = resolved_sym

    dispatch = {
        "status": cmd_status, "scan": cmd_scan, "news": cmd_news,
        "evaluate": cmd_evaluate, "plan": cmd_plan, "simulate": cmd_simulate,
        "size": cmd_size, "riskcheck": cmd_riskcheck, "quote": cmd_quote,
        "monitor": cmd_monitor, "enter": cmd_enter, "squareoff": cmd_squareoff,
        "recommend": cmd_recommend, "log": cmd_log,
    }

    result = dispatch[args.cmd](args)
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
