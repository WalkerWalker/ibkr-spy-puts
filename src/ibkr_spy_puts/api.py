"""FastAPI application for the trading dashboard.

Usage:
    poetry run uvicorn ibkr_spy_puts.api:app --reload --port 8000

Then open http://localhost:8000 in your browser.
"""

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

# Load environment variables
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent.parent / ".env")

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware

from ibkr_spy_puts.config import DatabaseSettings


class NoCacheMiddleware(BaseHTTPMiddleware):
    """Add no-cache headers to prevent browser caching."""

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        # Don't cache API responses or dashboard
        if request.url.path.startswith("/api") or request.url.path == "/":
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response
from ibkr_spy_puts.database import Database
from ibkr_spy_puts.connection_manager import (
    get_connection_manager,
    start_connection_manager,
    stop_connection_manager,
)

# Initialize FastAPI
app = FastAPI(
    title="IBKR SPY Put Strategy Dashboard",
    description="Monitor your put selling strategy",
    version="1.0.0",
)

# Add no-cache middleware
app.add_middleware(NoCacheMiddleware)


@app.on_event("startup")
async def startup_event():
    """Start the connection manager when the app starts."""
    start_connection_manager()


@app.on_event("shutdown")
async def shutdown_event():
    """Stop the connection manager when the app shuts down."""
    stop_connection_manager()

# Templates directory
templates_dir = Path(__file__).parent / "templates"
templates_dir.mkdir(exist_ok=True)
templates = Jinja2Templates(directory=str(templates_dir))


def get_db() -> Database:
    """Get database connection."""
    db = Database(settings=DatabaseSettings())
    db.connect()
    return db


def serialize_decimal(obj: Any) -> Any:
    """Convert Decimal to float for JSON serialization."""
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: serialize_decimal(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [serialize_decimal(v) for v in obj]
    return obj


# =============================================================================
# API Endpoints
# =============================================================================


@app.get("/api/positions")
async def get_positions():
    """Get all open positions."""
    db = get_db()
    try:
        positions = db.get_positions_for_display()
        return serialize_decimal(positions)
    finally:
        db.disconnect()


@app.get("/api/positions/live")
async def get_positions_live():
    """Get all open positions enriched with live IBKR data.

    Always fetches (available even when market is closed):
    - Unrealized P&L per position (from portfolio)
    - Maintenance margin per position (from whatIfOrder)

    Only fetches during market hours:
    - Greeks (delta, theta, gamma, vega) - requires live market data
    """
    import asyncio
    from ibkr_spy_puts.scheduler import MarketCalendar

    db = get_db()
    try:
        positions = db.get_positions_for_display()

        # Check if market is open
        calendar = MarketCalendar()
        market_is_open = calendar.is_market_open()

        # Always fetch P&L and margin (works even when market is closed)
        # Only fetch Greeks when market is open
        live_data = await asyncio.to_thread(
            _fetch_live_position_data,
            positions,
            fetch_greeks=market_is_open
        )

        # Check if we got data
        has_live_data = bool(live_data) and not live_data.get('error')

        # Enrich positions with live data
        enriched = []
        for pos in positions:
            pos_copy = dict(pos)
            # Handle expiration as date object or string
            exp = pos['expiration']
            if hasattr(exp, 'strftime'):
                exp_str = exp.strftime('%Y%m%d')
            else:
                exp_str = str(exp).replace('-', '')
            key = f"{pos['symbol']}_{int(pos['strike'])}_{exp_str}"

            if key in live_data and isinstance(live_data[key], dict):
                live = live_data[key]
                current_price = live.get('mid')
                pos_copy['current_price'] = current_price
                pos_copy['bid'] = live.get('bid')
                pos_copy['ask'] = live.get('ask')
                pos_copy['delta'] = live.get('delta')
                pos_copy['theta'] = live.get('theta')
                pos_copy['gamma'] = live.get('gamma')
                pos_copy['vega'] = live.get('vega')
                pos_copy['iv'] = live.get('iv')
                pos_copy['margin'] = live.get('margin')

                # Calculate P&L per position using entry price and current price
                # For short puts: profit when price goes down
                # P&L = (entry_price - current_price) * 100 * quantity
                if current_price is not None and pos['entry_price']:
                    entry = float(pos['entry_price'])
                    qty = pos['quantity']
                    # Per-position P&L (not aggregate from IBKR)
                    pnl = (entry - current_price) * 100 * qty
                    pos_copy['unrealized_pnl'] = round(pnl, 2)

                    # P&L percentage based on premium collected
                    premium_collected = entry * 100 * qty
                    if premium_collected > 0:
                        pnl_pct = (pnl / premium_collected) * 100
                        pos_copy['unrealized_pnl_pct'] = round(pnl_pct, 2)

            enriched.append(pos_copy)

        # Build response with metadata
        response = {
            "positions": serialize_decimal(enriched),
            "data_source": "live",
            "market_open": market_is_open,
        }

        return response
    finally:
        db.disconnect()


def _fetch_live_position_data(positions: list, fetch_greeks: bool = True) -> dict:
    """Fetch live data for positions from IBKR.

    Always fetches (works even when market is closed):
    - Unrealized P&L per position (from ib.portfolio())
    - Maintenance margin per position (from whatIfOrder)

    Only fetches when fetch_greeks=True (market is open):
    - Greeks (delta, theta, gamma, vega) from reqMktData

    Args:
        positions: List of position dicts from database
        fetch_greeks: Whether to fetch Greeks (only works during market hours)

    Returns:
        Dict mapping position keys to their live data
    """
    import subprocess
    import json

    if not positions:
        return {}

    from ibkr_spy_puts.config import TWSSettings
    tws_settings = TWSSettings()

    # Build list of contracts to fetch
    contracts_info = []
    for pos in positions:
        # Handle expiration as date object or string
        exp = pos['expiration']
        if hasattr(exp, 'strftime'):
            exp_str = exp.strftime('%Y%m%d')
        else:
            exp_str = str(exp).replace('-', '')
        contracts_info.append({
            'symbol': pos['symbol'],
            'strike': float(pos['strike']),
            'expiration': exp_str,
        })

    contracts_json = json.dumps(contracts_info)
    fetch_greeks_str = "True" if fetch_greeks else "False"

    # Script to fetch P&L, margin, and optionally Greeks
    script = f'''
import json
import asyncio
asyncio.set_event_loop(asyncio.new_event_loop())
from ib_insync import IB, Option, MarketOrder

ib = IB()
result = {{}}
fetch_greeks = {fetch_greeks_str}

try:
    ib.connect("{tws_settings.host}", {tws_settings.port}, clientId=97, readonly=True, timeout=15)

    contracts_info = {contracts_json}

    # Initialize result dict for each contract
    for info in contracts_info:
        key = f"{{info['symbol']}}_{{int(info['strike'])}}_{{info['expiration']}}"
        result[key] = {{}}

    # STEP 1: Get P&L from portfolio (always available)
    portfolio = ib.portfolio()
    for item in portfolio:
        c = item.contract
        if c.symbol == 'SPY' and c.secType == 'OPT' and getattr(c, 'right', '') == 'P':
            exp = getattr(c, 'lastTradeDateOrContractMonth', '')
            key = f"{{c.symbol}}_{{int(c.strike)}}_{{exp}}"
            if key in result:
                result[key]['unrealized_pnl'] = item.unrealizedPNL
                # Also get market value for current price calculation
                if item.marketValue and item.position:
                    result[key]['mid'] = abs(item.marketValue / item.position / 100)

    # STEP 2: Get positions for margin calculation
    positions = ib.positions()
    spy_puts = [p for p in positions if p.contract.symbol == 'SPY'
                and p.contract.secType == 'OPT'
                and getattr(p.contract, 'right', '') == 'P'
                and p.position < 0]

    # STEP 3: Get margin per position via whatIfOrder (always available)
    for pos in spy_puts:
        c = pos.contract
        qty = abs(int(pos.position))
        exp = getattr(c, 'lastTradeDateOrContractMonth', '')
        key = f"{{c.symbol}}_{{int(c.strike)}}_{{exp}}"

        qualified = ib.qualifyContracts(c)
        if qualified and key in result:
            order = MarketOrder("BUY", qty)
            whatif = ib.whatIfOrder(qualified[0], order)
            if whatif and whatif.maintMarginChange:
                maint_change = float(whatif.maintMarginChange)
                # Margin per contract (divide by quantity)
                margin_per_contract = (-maint_change if maint_change < 0 else 0) / qty
                result[key]['margin'] = margin_per_contract

    # STEP 4: Fetch Greeks only if requested (market is open)
    if fetch_greeks:
        ib.reqMarketDataType(3)  # Delayed data

        # Create and qualify all option contracts
        options = []
        for info in contracts_info:
            opt = Option(info['symbol'], info['expiration'], info['strike'], 'P', 'SMART')
            options.append(opt)

        qualified = ib.qualifyContracts(*options)

        # Request market data for all contracts
        tickers = []
        for opt in qualified:
            ticker = ib.reqMktData(opt, '106', False, False)
            tickers.append((opt, ticker))

        # Wait for data
        ib.sleep(5)

        # Collect Greeks
        for opt, ticker in tickers:
            key = f"{{opt.symbol}}_{{int(opt.strike)}}_{{opt.lastTradeDateOrContractMonth}}"
            if key in result:
                if ticker.bid and ticker.bid > 0:
                    result[key]['bid'] = ticker.bid
                if ticker.ask and ticker.ask > 0:
                    result[key]['ask'] = ticker.ask
                if result[key].get('bid') and result[key].get('ask'):
                    result[key]['mid'] = (result[key]['bid'] + result[key]['ask']) / 2

                if ticker.modelGreeks:
                    g = ticker.modelGreeks
                    result[key]['delta'] = g.delta
                    result[key]['theta'] = g.theta
                    result[key]['gamma'] = g.gamma
                    result[key]['vega'] = g.vega
                    result[key]['iv'] = g.impliedVol

        # Cancel market data subscriptions
        for opt, ticker in tickers:
            ib.cancelMktData(opt)

    ib.disconnect()
except Exception as e:
    result['error'] = str(e)

print(json.dumps(result))
'''

    try:
        proc = subprocess.run(
            ["python", "-c", script],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return json.loads(proc.stdout.strip())
    except Exception:
        pass

    return {}


@app.get("/api/summary")
async def get_summary():
    """Get strategy summary metrics."""
    db = get_db()
    try:
        summary = db.get_strategy_summary()
        return serialize_decimal(summary)
    finally:
        db.disconnect()


@app.get("/api/trade-history")
async def get_trade_history():
    """Get trade execution history.

    Returns a log of all executed trades (entries and exits).
    """
    db = get_db()
    try:
        history = db.get_trade_history()
        return serialize_decimal(history)
    finally:
        db.disconnect()


@app.get("/api/spy-price")
async def get_spy_price():
    """Get current SPY price and daily change.

    Returns SPY last price, previous close, and calculated daily change.
    Uses the persistent connection manager's cached data.
    """
    manager = get_connection_manager()
    return manager.get_spy_price()


def _fetch_spy_price() -> dict:
    """Fetch SPY price from IBKR."""
    import subprocess
    import json

    from ibkr_spy_puts.config import TWSSettings
    tws_settings = TWSSettings()

    script = f'''
import json
import asyncio
asyncio.set_event_loop(asyncio.new_event_loop())
from ib_insync import IB, Stock

ib = IB()
result = {{"error": None}}

try:
    ib.connect("{tws_settings.host}", {tws_settings.port}, clientId=98, readonly=True, timeout=10)
    ib.reqMarketDataType(3)  # Delayed if real-time not available

    spy = Stock("SPY", "SMART", "USD")
    ib.qualifyContracts(spy)
    ticker = ib.reqMktData(spy, "", False, False)
    ib.sleep(2)

    if ticker.last and ticker.last > 0:
        result["price"] = ticker.last
    elif ticker.bid and ticker.bid > 0:
        result["price"] = (ticker.bid + ticker.ask) / 2

    if ticker.close and ticker.close > 0:
        result["close"] = ticker.close

    if result.get("price") and result.get("close"):
        change = result["price"] - result["close"]
        pct = (change / result["close"]) * 100
        result["change"] = round(change, 2)
        result["change_pct"] = round(pct, 2)

    ib.cancelMktData(spy)
    ib.disconnect()
except Exception as e:
    result["error"] = str(e)

print(json.dumps(result))
'''

    try:
        proc = subprocess.run(
            ["python", "-c", script],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return json.loads(proc.stdout.strip())
    except Exception as e:
        return {"error": str(e)}

    return {"error": "Failed to fetch SPY price"}


@app.get("/api/snapshots")
async def get_snapshots(limit: int = 30):
    """Get recent daily book snapshots.

    Returns historical P&L, Greeks, and margin data captured at market close.
    """
    db = get_db()
    try:
        snapshots = db.get_snapshots(limit=limit)
        return serialize_decimal(snapshots)
    finally:
        db.disconnect()


# =============================================================================
# Connection Status & Live Orders
# =============================================================================


def _check_connection_via_socket():
    """Check TWS connection using simple socket test."""
    import socket
    import os

    from ibkr_spy_puts.config import TWSSettings, ScheduleSettings
    from ibkr_spy_puts.scheduler import MarketCalendar

    tws_settings = TWSSettings()
    schedule_settings = ScheduleSettings(
        trade_time=os.getenv("SCHEDULE_TRADE_TIME", "09:30"),
        timezone=os.getenv("SCHEDULE_TIMEZONE", "America/New_York"),
    )

    result = {
        "connection": {
            "connected": False,
            "logged_in": False,
            "account": None,
            "trading_mode": None,
            "ready_to_trade": False,
            "tws_host": tws_settings.host,
            "tws_port": tws_settings.port,
            "next_trade_time": schedule_settings.trade_time,
            "timezone": schedule_settings.timezone,
            "error": None,
        },
        "live_orders": [],
        "ibkr_positions": [],
    }

    # Simple socket test to check if the port is open
    # Note: This only means the port is listening, not that IBKR is logged in
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect((tws_settings.host, tws_settings.port))
        sock.close()
        result["connection"]["connected"] = True
        # Don't set logged_in or ready_to_trade here - wait for actual IBKR verification
    except Exception as e:
        result["connection"]["error"] = str(e)

    # Check if today is a trading day
    try:
        calendar = MarketCalendar()
        result["connection"]["is_trading_day"] = calendar.is_trading_day()
    except Exception:
        result["connection"]["is_trading_day"] = True

    return result


async def get_connection_and_orders():
    """Get TWS connection status and live orders from the connection manager.

    Uses the persistent connection manager instead of spawning subprocesses.
    """
    import os
    from ibkr_spy_puts.config import ScheduleSettings

    schedule_settings = ScheduleSettings(
        trade_time=os.getenv("SCHEDULE_TRADE_TIME", "09:30"),
        timezone=os.getenv("SCHEDULE_TIMEZONE", "America/New_York"),
    )

    manager = get_connection_manager()
    data = manager.get_all()

    # Add schedule info to connection status
    data["connection"]["tws_host"] = manager.settings.host
    data["connection"]["tws_port"] = manager.settings.port
    data["connection"]["next_trade_time"] = schedule_settings.trade_time
    data["connection"]["timezone"] = schedule_settings.timezone

    # Check if today is a trading day
    try:
        from ibkr_spy_puts.scheduler import MarketCalendar
        calendar = MarketCalendar()
        data["connection"]["is_trading_day"] = calendar.is_trading_day()
    except Exception:
        data["connection"]["is_trading_day"] = True

    return data


@app.get("/api/connection-status")
async def api_connection_status():
    """Check TWS/Gateway connection status and trading readiness."""
    result = await get_connection_and_orders()
    return result["connection"]


@app.post("/api/gateway/restart")
async def restart_gateway():
    """Restart the IB Gateway container to trigger re-authentication.

    This restarts the ib-gateway Docker container which will prompt for 2FA.
    Uses the Docker socket API directly (works from inside containers).
    """
    import asyncio
    import socket
    import http.client

    def restart_via_docker_socket():
        """Call Docker API via Unix socket to restart the gateway container."""
        container_name = "ibkr-gateway"
        socket_path = "/var/run/docker.sock"

        # Check if Docker socket exists
        if not Path(socket_path).exists():
            return {"success": False, "error": "Docker socket not available"}

        # Create connection to Docker socket
        class DockerSocket(http.client.HTTPConnection):
            def __init__(self):
                super().__init__("localhost")

            def connect(self):
                self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self.sock.connect(socket_path)

        try:
            conn = DockerSocket()
            conn.request("POST", f"/containers/{container_name}/restart?t=10")
            response = conn.getresponse()

            if response.status == 204:
                return {"success": True, "message": "Gateway restart initiated"}
            elif response.status == 404:
                return {"success": False, "error": f"Container {container_name} not found"}
            else:
                body = response.read().decode()
                return {"success": False, "error": f"Docker API error: {response.status} - {body}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    try:
        result = await asyncio.to_thread(restart_via_docker_socket)
        return result
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/api/live-orders")
async def api_live_orders():
    """Get all live orders from IBKR."""
    result = await get_connection_and_orders()
    return {"orders": result["live_orders"], "connected": result["connection"]["connected"]}


@app.get("/api/executions")
async def api_executions():
    """Get recent executions with commission data from IBKR.

    Returns fills from the current session including commission info.
    """
    import asyncio
    import subprocess
    import json

    from ibkr_spy_puts.config import TWSSettings
    tws_settings = TWSSettings()

    script = f'''
import json
import asyncio
asyncio.set_event_loop(asyncio.new_event_loop())
from ib_insync import IB, ExecutionFilter
from datetime import datetime, timedelta

ib = IB()
result = {{"executions": [], "connected": False, "error": None}}

try:
    ib.connect("{tws_settings.host}", {tws_settings.port}, clientId=99, readonly=True, timeout=15)
    result["connected"] = True

    # Request executions from the last 7 days using filter
    # IBKR format: YYYYMMDD-HH:MM:SS
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y%m%d-00:00:00")
    exec_filter = ExecutionFilter(time=week_ago, symbol="SPY", secType="OPT")

    fills = ib.reqExecutions(exec_filter)
    ib.sleep(3)

    for fill in fills:
        c = fill.contract
        e = fill.execution
        cr = fill.commissionReport

        exec_data = {{
            "symbol": c.symbol,
            "strike": c.strike,
            "expiration": c.lastTradeDateOrContractMonth,
            "right": c.right,
            "action": e.side,  # BOT or SLD
            "quantity": int(e.shares),
            "price": e.price,
            "exec_time": e.time.isoformat() if e.time else None,
            "exec_id": e.execId,
            "order_id": e.orderId,
            "commission": None,
            "realized_pnl": None,
        }}

        if cr:
            exec_data["commission"] = cr.commission
            exec_data["realized_pnl"] = cr.realizedPNL if cr.realizedPNL else None

        result["executions"].append(exec_data)

    ib.disconnect()
except Exception as e:
    result["error"] = str(e)

print(json.dumps(result))
'''

    try:
        proc = await asyncio.to_thread(
            subprocess.run,
            ["python", "-c", script],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return json.loads(proc.stdout.strip())
        else:
            return {"executions": [], "connected": False, "error": proc.stderr}
    except Exception as e:
        return {"executions": [], "connected": False, "error": str(e)}


# =============================================================================
# Dashboard Pages
# =============================================================================


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Main dashboard page."""
    db = get_db()
    try:
        positions = db.get_positions_for_display()
        summary = db.get_strategy_summary()
        trade_history = db.get_trade_history()

        # Get connection status and live orders in one call
        ibkr_data = await get_connection_and_orders()

        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "positions": positions,
                "summary": summary,
                "trade_history": trade_history,
                "connection": ibkr_data["connection"],
                "live_orders": ibkr_data["live_orders"],
                "ibkr_positions": ibkr_data["ibkr_positions"],
                "now": datetime.now,
            },
        )
    finally:
        db.disconnect()


@app.get("/health")
async def health():
    """Health check endpoint."""
    db = get_db()
    try:
        # Simple query to verify database connection
        db.get_strategy_summary()
        return {"status": "healthy", "database": "connected"}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}
    finally:
        db.disconnect()


@app.get("/api/debug/margin-comparison")
async def debug_margin_comparison():
    """Compare margin calculation methods.

    Compares:
    1. Sum of individual whatIfOrder per position
    2. Grouped whatIfOrder (all contracts of same strike at once)
    """
    import asyncio
    import subprocess
    import json

    from ibkr_spy_puts.config import TWSSettings
    tws_settings = TWSSettings()

    script = f'''
import json
import asyncio
asyncio.set_event_loop(asyncio.new_event_loop())
from ib_insync import IB, MarketOrder
from collections import defaultdict

ib = IB()
result = {{"individual": [], "grouped": [], "individual_total": 0, "grouped_total": 0}}

try:
    ib.connect("{tws_settings.host}", {tws_settings.port}, clientId=94, readonly=True, timeout=15)

    positions = ib.positions()
    spy_puts = [p for p in positions if p.contract.symbol == "SPY"
                and p.contract.secType == "OPT"
                and getattr(p.contract, "right", "") == "P"
                and p.position < 0]

    result["position_count"] = len(spy_puts)

    # Method 1: Individual whatIfOrder for each position
    for pos in spy_puts:
        c = pos.contract
        qty = abs(int(pos.position))
        qualified = ib.qualifyContracts(c)
        if not qualified:
            continue
        order = MarketOrder("BUY", qty)
        whatif = ib.whatIfOrder(qualified[0], order)
        if whatif and whatif.maintMarginChange:
            maint_change = float(whatif.maintMarginChange)
            margin = -maint_change if maint_change < 0 else 0
            result["individual"].append({{"strike": c.strike, "qty": qty, "margin": round(margin, 2)}})
            result["individual_total"] += margin

    # Method 2: Grouped by contract (close all of same strike at once)
    grouped = defaultdict(int)
    contracts_map = {{}}
    for pos in spy_puts:
        c = pos.contract
        key = (c.lastTradeDateOrContractMonth, c.strike)
        grouped[key] += abs(int(pos.position))
        contracts_map[key] = c

    for key, total_qty in grouped.items():
        contract = contracts_map[key]
        qualified = ib.qualifyContracts(contract)
        if not qualified:
            continue
        order = MarketOrder("BUY", total_qty)
        whatif = ib.whatIfOrder(qualified[0], order)
        if whatif and whatif.maintMarginChange:
            maint_change = float(whatif.maintMarginChange)
            margin = -maint_change if maint_change < 0 else 0
            result["grouped"].append({{"strike": contract.strike, "qty": total_qty, "margin": round(margin, 2)}})
            result["grouped_total"] += margin

    result["individual_total"] = round(result["individual_total"], 2)
    result["grouped_total"] = round(result["grouped_total"], 2)
    result["difference"] = round(abs(result["individual_total"] - result["grouped_total"]), 2)

    # Check VOO margin - what would be released if we close all VOO?
    voo_margin = None
    for pos in ib.positions():
        c = pos.contract
        if c.symbol == "VOO" and c.secType == "STK":
            qty = abs(int(pos.position))
            qualified = ib.qualifyContracts(c)
            if qualified:
                # SELL to close long stock position
                order = MarketOrder("SELL", qty)
                whatif = ib.whatIfOrder(qualified[0], order)
                if whatif and whatif.maintMarginChange:
                    maint_change = float(whatif.maintMarginChange)
                    voo_margin = -maint_change if maint_change < 0 else 0
                    result["voo"] = {{
                        "symbol": "VOO",
                        "quantity": qty,
                        "margin_released": round(voo_margin, 2),
                        "maint_margin_change": round(maint_change, 2)
                    }}
            break

    # Check current account margin
    account = ib.managedAccounts()[0]
    account_values = ib.accountValues(account)

    current_maint = None
    for av in account_values:
        if av.tag == "MaintMarginReq" and av.currency == "USD":
            current_maint = float(av.value)
            break

    result["account_current_maint_margin"] = round(current_maint, 2) if current_maint else None

    # Calculate implied SPY puts margin from group
    if voo_margin and result.get("grouped_total"):
        usidx_group = 48555  # From margin report
        implied_spy_margin = usidx_group - voo_margin
        result["implied_spy_puts_margin"] = round(implied_spy_margin, 2)
        result["usidx_group_margin"] = usidx_group

    result["note"] = "voo.margin_released = whatIfOrder for closing all VOO. implied_spy_puts_margin = USIDX group - VOO margin."

    ib.disconnect()
except Exception as e:
    result["error"] = str(e)

print(json.dumps(result))
'''

    try:
        proc = await asyncio.to_thread(
            subprocess.run,
            ["python", "-c", script],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return json.loads(proc.stdout.strip())
        else:
            return {"error": proc.stderr or "No output", "stdout": proc.stdout}
    except Exception as e:
        return {"error": str(e)}
