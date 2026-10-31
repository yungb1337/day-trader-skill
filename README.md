# The Day Trader skill

11 Claude skills for day trading: scan thousands of charts, track breaking news, time every entry and exit, and replay every outcome before you risk a dollar. Full native support for both **US Markets (NYSE & Nasdaq)** and **Indian Markets (NSE & BSE)**.

Free, MIT, no signup, no API key. The tools are plain Python that read free
public data: Yahoo Finance charts, Nasdaq Trader's symbol lists, NSE India equity & NIFTY 500 lists,
SEC EDGAR, the House Clerk, and NSE Bulk/Block deals.

One scans thousands of charts for setups. Another tracks breaking news on
every stock you watch. One watches a setup bar by bar and tells you the moment
to get in, and another sets your stop and your target before you do. And the
craziest one is the Time Machine: before you risk a dollar, it replays the
rest of the session 10,000 times from the stock's own recent bars, plus every
past day the same setup fired, and tells you the honest odds.

And no trade goes through until you say yes. Nothing in this pack can place
an order on its own. Paper trading is the default.

> **Looking for the original Trader skill?** Copy an investor's 13F, the
> stock screener, tax lots and rebalancing live in
> [github.com/Jakeschincariol/trader-skill](https://github.com/Jakeschincariol/trader-skill).
> This repo is its day-trading sequel: the Trader skill v2.

## Install

Paste this into Claude:

```
https://github.com/Jakeschincariol/day-trader-skill

install skill
```

Or as a plugin, in Claude Code:

```
/plugin marketplace add Jakeschincariol/day-trader-skill
/plugin install day-trader-skill@day-trader-skill
```

Claude Code namespaces plugin skills, so installed as a plugin they show up as
`/day-trader-skill:trader-scanner` and so on. Copy the folders instead if you want
plain `/trader-scanner`:

```bash
git clone https://github.com/Jakeschincariol/day-trader-skill.git
cp -r day-trader-skill/trader-* ~/.claude/skills/
```

Project-local instead of global: copy the same folders into your repo's
`.claude/skills/`. No Claude Code at all? Paste any single `SKILL.md` at the
top of a chat and it runs as a mode. You lose the Python tools, but the method
works.

The tools need Python 3.8 or newer. Nothing to pip install. The SEC tools
(news filings, the analyst) ask you to identify yourself: put your name and
email in `trader/config.json` (`"sec_user_agent": "Your Name you@example.com"`)
or in the `SEC_USER_AGENT` environment variable.

## The eleven

| command | job | what it does |
| --- | --- | --- |
| `/trader-scanner` | Chart Scanner | Scans thousands of charts for setups: opening-range breakouts, flags, oversold bounces (US & NSE/BSE). |
| `/trader-news` | News Monitor | Tracks breaking news on every stock you watch and flags what moves it (Yahoo US/IN feeds & regulatory tags). |
| `/trader-entry` | Entry Timer | Watches a setup bar by bar and tells you the moment to get in (09:30-09:45 ET for US, 09:15-09:30 IST for India). |
| `/trader-exit` | Exit Planner | Sets your stop and your target before you enter (flat by 15:55 ET for US, 15:15 IST for India). |
| `/trader-timemachine` | Time Machine | Replays every possible outcome of a trade before you risk a dollar: 10,000 simulated sessions (78 bars US, 75 bars India). |
| `/trader-risk` | Risk Manager | Checks every trade against your limits (PDT bypassed for India, Zerodha & Alpaca validation). |
| `/trader-budget` | Budget Manager | Sizes every position to your budget and max loss (calculates STT, GST, SEBI, Stamp Duty & NSE charges). |
| `/trader-broker` | Order Executor | Sends approved orders to your broker (Alpaca, IBKR, or Zerodha Kite Connect), paper trading by default, never without your yes. |
| `/trader-alerts` | Price Alerts | Rings when a price, a stop or the opening bell hits (NYSE & NSE trading calendars and holidays). |
| `/trader-analyst` | Earnings Analyst | Reads earnings reports straight from EDGAR or Yahoo Finance quoteSummary (INR Crores & Lakhs). |
| `/trader-pelosi` | Pelosi / Super-Investor Tracker | Reads Congress's disclosures & Indian Super-Investors (Damani, Jhunjhunwala, Kedia, etc.) plus NSE Bulk/Block deals. |

## Indian Market Support (NSE & BSE)

The entire toolset supports Indian markets natively via `--market in` (or `--market nse`/`--market bse`), symbol suffixes (`.NS`/`.BO`), or `"market": "in"` in `trader/config.json`:

- **Market Sessions**: 09:15 to 15:30 IST regular trading hours; 09:00 to 09:08 IST pre-market.
- **Intraday Square-off**: Mandatory auto-square-off at 15:15 IST (`flat_by: "15:15"`) for MIS intraday positions.
- **Timezone**: Indian Standard Time (IST, UTC+05:30 fixed offset without daylight saving).
- **Trading Calendars**: Official NSE/BSE trading holidays (Republic Day, Mahashivratri, Holi, Diwali, Eid, Independence Day, etc.) and Muhurat Trading.
- **Tickers**: Preserves `.NS` and `.BO` extensions with literal dots for Yahoo Finance quotes (`RELIANCE.NS`, `TCS.NS`, `INFY.BO`).
- **Universes**: Official NSE Equity list (`EQUITY_L.csv`) filtered to Series EQ and NIFTY 500 (`ind_nifty500list.csv`).
- **Broker & Order Formatting**: Zerodha Kite Connect order specifications (`exchange: "NSE"`, `tradingsymbol: "RELIANCE"`, `product: "MIS"`, `validity: "DAY"`).
- **Statutory Charges**: Complete breakdown of STT (0.025% intraday sell), NSE turnover charges (0.00297%), SEBI turnover fees (₹10/cr), stamp duty (0.003%), GST (18%), and broker commissions.
- **Risk & Regulatory Rules**: US SEC/FINRA PDT rule ($25,000 threshold and 3-trades-per-5-days rule) is bypassed for Indian markets as SEBI rules apply. INR limits for order size and max loss.
- **Financial Analysis**: Financials presented in Indian denominations: Crores (`₹ Cr` = 10^7) and Lakhs (`₹ L` = 10^5).
- **Super-Investors & Deal Tracking**: Tracks prominent Indian investors (Radhakishan Damani, Rakesh Jhunjhunwala / Rare Enterprises, Vijay Kedia, Ashish Kacholia, Nemish Shah, Raamdeo Agrawal, Porinju Veliyath, Dolly Khanna) and NSE/BSE Bulk & Block Deals.

## Automated Agent & Asynchronous Daemon

Beyond running individual skills by hand, this repository includes a unified pipeline orchestrator and an asynchronous background daemon designed for live intraday trading:

### 1. One-Click Orchestrator (`trader/agent.py`)
Chains the full pipeline in a single execution:
`alerts (clock/calendar) -> scan (ORB/daily) -> news (catalysts/SEBI flags) -> entry (trigger) -> exit (stop/target) -> timemachine (10k Monte Carlo odds) -> budget (sizing & taxes) -> risk (gatekeeper)`

Outputs an **Executive Trade Recommendation Card**:
- **What to Buy**: Symbol, exchange, MIS product, quantity, entry price.
- **Why Behind It**: Technical breakout confirmation, Monte Carlo win probability, Profit Factor, Expected Value.
- **Target & Key Levels**: Stop loss, 1.5R/2R targets, exact breakdown of Indian statutory charges (STT, GST, SEBI, stamp duty, brokerage), and net expected profit.
- **One-Click Order**: Ready-to-send Zerodha Kite Connect JSON payload.

```bash
# Run full automated analysis for a stock:
python3 trader/agent.py --symbol INFY.NS

# Replay a specific historical moment:
python3 trader/agent.py --symbol INFY.NS --now "2026-10-30 11:05"
```

### 2. Background Asynchronous Daemon (`trader/daemon.py`)
A non-blocking `asyncio` process that supervises live positions:
- **State Machine**: `IDLE` -> `WAITING_TRIGGER` -> `ENTERING` -> `IN_POSITION` -> `CLOSING` -> `CLOSED`.
- **Live Tick Monitoring**: Continuously evaluates incoming prices against Stop-Loss and Take-Profit levels.
- **15:15 IST Mandatory Square-Off**: Automatically flattens any open MIS position at 15:15 IST, protecting you from broker penalty fees.
- **Telemetry**: Writes live P&L and status updates atomically to `trader/state/live_trade.json`.

```bash
# Start background monitoring (paper or live):
python3 trader/daemon.py run --symbol INFY.NS --interval 10 &

# Inspect live status and floating P&L:
python3 trader/daemon.py status

# Stop daemon and immediately square off:
python3 trader/daemon.py stop
```

### 3. Continuous Monitoring & Persistent Agent (`/loop` & `trader/orchestrator.py`)
In Claude Code, you can run autonomous recurring checks or let Claude orchestrate the entire intraday session:

```bash
# Periodic daemon check:
/loop 1m python3 trader/daemon.py status

# Persistent agentic loop (Claude reasons at every tick):
/loop 30s /trader-orchestrator
```

The persistent orchestrator CLI (`trader/orchestrator.py`) provides structured JSON actions:
- `status`: Market clock, session phase, open position
- `scan`: ORB scanner on liquid NSE universe
- `news`: Live headline catalyst and regulatory check
- `evaluate`: Real-time 5-minute candle breakout confirmation
- `plan`: Midpoint stop-loss and 1.5R target calculation
- `simulate`: 10,000-path Monte Carlo Time Machine
- `size`: Position sizing and Indian statutory charges breakdown
- `riskcheck`: Risk limits and duplicate trade gatekeeper
- `monitor`: Real-time tick evaluation vs stop, target, and 15:15 IST cutoff
- `enter` & `squareoff`: Paper trade fills and realized P&L accounting

---

## Research & Intelligence Sources (What Actually Matters)

Professional intraday desks avoid social media noise and focus on four high-signal tiers:

1. **Primary Exchange Disclosures (100% Signal)**:
   - **NSE Corporate Announcements (`nseindia.com`)**: Real-time regulatory filings (Regulation 30) for earnings, order wins, and board meetings.
   - **NSE Bulk & Block Deals (17:30 IST daily)**: Tracks institutional buying/selling (>0.5% equity).
   - **Promoter Disclosures (SAST)**: Tracks when company founders buy shares in the open market.
2. **Real-Time Financial Catalysts**:
   - **Moneycontrol Pro / Live Markets**: Rapid scorecards during earnings season and intraday management concall summaries.
   - **Economic Times (ET Markets)**: Corporate developments, policy reforms, and sector flows.
   - **CNBC-TV18 & LiveMint**: Fast macro alerts on RBI repo rate decisions, CPI inflation, and GST revenue.
3. **Pre-Market Macro Cues (08:00 - 09:00 IST)**:
   - **GIFT Nifty (formerly SGX Nifty)**: Traded in GIFT City from 06:30 IST; directly signals whether Nifty opens gap-up or gap-down.
   - **US Market Close (Nasdaq & S&P 500)**: Direct overnight correlation with Indian IT and export stocks.
   - **Brent Crude Oil & USD/INR**: Critical drivers of inflation, domestic currency strength, and foreign fund (FII) flows.
4. **Fundamental Quality & Institutional Ownership**:
   - **Screener.in**: Clean 10-year financials, ROCE/ROE, and searchable conference call transcripts.
   - **Trendlyne**: Tracks Indian Super-Investor portfolios (Damani, Jhunjhunwala estate, Kedia, Kacholia, Nemish Shah).

## How to use it

Everything lands in a `trader/` folder in your project, and each skill reads
what the last one wrote:

```
scanner -> news -> entry -> exit -> timemachine -> budget -> risk -> broker   (one trade)
alerts: stop, target, flat by the close                                       (while it is open)
news / analyst / pelosi                                                        (on a schedule)
```

An example, the trade in the reel: Tesla, Monday October 19 2026, real data.

1. **`/trader-scanner`** at 10:15 AM ET: of the 231 charts in the reel's
   scan, 42 had closed above their 15-minute opening range. TSLA is one: range 354.47 to 362.18,
   the 10:10 candle closed at 364.18, 0.55 % over the top.
2. **`/trader-news`**: the morning's TSLA headlines, in New York time
   ("Tesla-SpaceX Merger Mania Strikes Wall Street Ahead of Earnings", 8:04,
   tagged earnings and deal). Tesla reports this week: a day trade is out
   before the close, so it is not exposed to the report.
3. **`/trader-entry`** watched TSLA bar by bar. At 10:12 it said waiting (the
   10:10 bar was still forming). At 10:15 it said triggered: the first
   5-minute close above the range, 364.18.
4. **`/trader-exit`** set the plan before the entry: stop at the range's
   midpoint, 358.32, so 5.86 a share at risk; target one times that risk,
   370.04; flat by 3:55 PM either way.
5. **`/trader-timemachine`** replayed the rest of the day 10,000 times, each
   5-minute candle drawn from TSLA's own candles at that time of day over the
   previous 59 sessions (seed 19, so anyone can repeat it): target first
   29.4 %, stop first 25.5 %, still open at the close 45.1 %. It also re-ran
   the same rule on those 59 real sessions: it fired 33 times, 13 hit the
   target, 15 the stop, 5 neither. Its verdict: close to a coin flip. That is
   the point of looking before you risk a dollar.
6. **`/trader-budget`** with a $5,000 account and a $50 max loss per trade:
   50 / 5.86 = 8 shares ($2,913.44). If the stop fills at 358.32, the loss is
   $46.88. It writes one bracket order: buy 8 TSLA, stop-loss 358.32,
   take-profit 370.04.
7. **`/trader-risk`** checks it against your limits: the max loss, the daily
   loss limit, trades per day, reward to risk, the entry window, position
   size and cash. PASS or BLOCK, with the reason.
8. **`/trader-broker`** shows you the ticket in plain words. You type yes for
   that exact order. Claude calls your brokerage connector (Alpaca's MCP
   server, paper account by default), and Claude's own "Allow once / Deny"
   card asks you one more time.
9. **`/trader-alerts`** rings on the stop, the target and at 3:55 PM to
   flatten whatever is still open.

## The tools

All standard-library Python. The data tools read public endpoints and are
polite about it.

```bash
# US Market Commands:
python3 trader-scanner/scan.py universe                                # ~5,900 US stocks, Nasdaq Trader
python3 trader-scanner/scan.py daily                                   # pre-market setups, whole universe
python3 trader-scanner/scan.py orb --at 10:15                          # opening-range breakouts so far
python3 trader-news/news.py --tickers TSLA --hours 12                  # headlines, 8-Ks, today's move
python3 trader-entry/entry.py check TSLA --setup orb                   # waiting / triggered / expired
python3 trader-entry/entry.py watch TSLA --every 30                    # bar by bar until it triggers
python3 trader-exit/exit.py plan --stop or-mid --target 1R --write     # -> trader/plan.json
python3 trader-timemachine/timemachine.py run --write                  # 10,000 replays + the history
python3 trader-budget/size.py --write                                  # -> trader/orders.json
python3 trader-risk/check.py                                           # PASS / BLOCK per order
python3 trader-broker/ticket.py show                                   # the tickets, READY or not
python3 trader-alerts/alerts.py add --from-plan                        # stop, target, flat by close
python3 trader-alerts/alerts.py check                                  # rings what hit, once
python3 trader-analyst/facts.py TSLA                                   # quarters, growth, EPS
python3 trader-pelosi/ptr.py index --member pelosi                     # her PTRs this year

# Indian Market Commands (--market in):
python3 trader-scanner/scan.py universe --market in                    # NIFTY 500 / NSE Equity universe
python3 trader-scanner/scan.py orb --market in --at 10:00              # 09:15-09:30 range breakout
python3 trader-news/news.py --tickers RELIANCE.NS --hours 12           # Indian headlines, regulatory tags
python3 trader-entry/entry.py check RELIANCE.NS --write                # IST timestamps & Indian market session
python3 trader-exit/exit.py plan --flat-by 15:15 --write               # auto-square-off at 15:15 IST
python3 trader-timemachine/timemachine.py run --market in --write      # 75 5-min bars (09:15-15:30 IST)
python3 trader-budget/size.py --config config.india.example.json       # STT, NSE, SEBI, GST breakdown
python3 trader-risk/check.py                                           # PDT bypassed, Zerodha validation
python3 trader-broker/ticket.py show                                   # Zerodha Kite Connect order format
python3 trader-alerts/alerts.py clock --market in                      # NSE/BSE clock & holidays
python3 trader-analyst/facts.py RELIANCE.NS                            # Financials in ₹ Crores & Lakhs
python3 trader-pelosi/ptr.py deals --investor damani                   # Indian Super-Investors & Bulk Deals
```

```bash
python3 -m unittest discover -s tests -v
```

The tests run offline, on fixtures cut from real data: TSLA's 5-minute bars
for the 60 days to October 19 2026, the 10:15 AM opening-range scan, Yahoo's
TSLA headlines that morning, the House Clerk's 2026 index, real PTR text and
real XBRL facts. They reproduce every number in the example above.

## Fine print

**Not financial advice.** This is software that reads public data and does
arithmetic. It does not know your situation and it cannot see the future.
Most people who day trade lose money. You can lose money. Decide for
yourself, and talk to a licensed professional if you need advice.

**No trade goes through until you say yes.** Only `/trader-broker` can lead to
an order, and only after `/trader-risk` passes that exact order and you type
yes for it in the chat. Set your brokerage tool to "Needs approval" in Claude
(Customize, Connectors, tool permissions), never "Always allow", so Claude's
own "Allow once / Deny" card is a second check. No options, margin, short
selling or leverage unless you turn them on.

**Paper first.** `"paper": true` is the default in `config.example.json`, and
Alpaca's MCP server defaults to its paper account. Keys live in environment
variables, never in the chat.

**The Time Machine is a simulation, not a prediction.** It is built from the
stock's own last 60 days of 5-minute bars. The future can do things those 60
days never did: an earnings report, a halt, a headline. Treat its odds as a
reality check, never as a promise.

**A stop is a plan, not a guarantee.** In a fast market or across a gap, a
stop order can fill well past its price. The max loss shown is the loss at
the stop price.

**The data is free and unofficial.** Yahoo Finance's chart endpoint is not an
official API: it can lag, change or rate-limit you. The scanner and the entry
timer read 1-minute or 5-minute bars, not every tick. Check anything that
matters against your broker before you act on it.

**Day-trading rules.** FINRA removed the $25,000 pattern-day-trader minimum
on June 4, 2026 (Regulatory Notice 26-10), and brokers may phase the change
in until October 20, 2027, so yours may still apply the old rule. If it does,
set `"pdt_guard": true` and `/trader-risk` counts your day trades.

**Congress's trades are public, not inside information.** Members must disclose
trades over $1,000 within 45 days (the STOCK Act). The Pelosi skill reads those
public filings the minute they post.

**Taxes.** Day-trading gains are short-term gains, and frequent trading makes
wash sales likely. Nothing here is tax advice.

## Files

```
trader-scanner/     SKILL.md, scan.py
trader-news/        SKILL.md, news.py
trader-entry/       SKILL.md, entry.py
trader-exit/        SKILL.md, exit.py
trader-timemachine/ SKILL.md, timemachine.py
trader-risk/        SKILL.md, check.py
trader-budget/      SKILL.md, size.py, config.example.json, targets.example.json
trader-broker/      SKILL.md, ticket.py, claude_desktop_config.example.json
trader-alerts/      SKILL.md, alerts.py, alerts.example.json
trader-analyst/     SKILL.md, facts.py, metrics.example.json
trader-pelosi/      SKILL.md, ptr.py
trader/             agent.py, daemon.py
tests/              unittest suite + offline fixtures
```

MIT. Made by [Jake Schincariol](https://opusjake.ai).
