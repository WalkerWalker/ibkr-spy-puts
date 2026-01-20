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

from ibkr_spy_puts.config import DatabaseSettings
from ibkr_spy_puts.database import Database

# Initialize FastAPI
app = FastAPI(
    title="IBKR SPY Put Strategy Dashboard",
    description="Monitor your put selling strategy",
    version="1.0.0",
)

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
    """Get all open positions enriched with live IBKR data (price, Greeks, P&L)."""
    import asyncio

    db = get_db()
    try:
        positions = db.get_positions_for_display()

        # Fetch live data from IBKR via subprocess
        live_data = await asyncio.to_thread(_fetch_live_position_data, positions)

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

            if key in live_data:
                live = live_data[key]
                pos_copy['current_price'] = live.get('mid')
                pos_copy['bid'] = live.get('bid')
                pos_copy['ask'] = live.get('ask')
                pos_copy['delta'] = live.get('delta')
                pos_copy['theta'] = live.get('theta')
                pos_copy['gamma'] = live.get('gamma')
                pos_copy['vega'] = live.get('vega')
                pos_copy['iv'] = live.get('iv')
                pos_copy['margin'] = live.get('margin')

                # Calculate P&L
                if live.get('mid') and pos['entry_price']:
                    entry = float(pos['entry_price'])
                    current = float(live['mid'])
                    # For short puts: profit when price goes down
                    pnl_per_contract = (entry - current) * 100  # Options are 100 shares
                    pnl_pct = ((entry - current) / entry) * 100
                    pos_copy['unrealized_pnl'] = round(pnl_per_contract * pos['quantity'], 2)
                    pos_copy['unrealized_pnl_pct'] = round(pnl_pct, 2)

            enriched.append(pos_copy)

        return serialize_decimal(enriched)
    finally:
        db.disconnect()


def _fetch_live_position_data(positions: list) -> dict:
    """Fetch live data for positions from IBKR."""
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

    script = f'''
import json
import asyncio
asyncio.set_event_loop(asyncio.new_event_loop())
from ib_insync import IB, Option, MarketOrder

ib = IB()
result = {{}}

try:
    ib.connect("{tws_settings.host}", {tws_settings.port}, clientId=97, readonly=True, timeout=15)
    ib.reqMarketDataType(3)  # Delayed data

    contracts_info = {contracts_json}

    for info in contracts_info:
        key = f"{{info['symbol']}}_{{int(info['strike'])}}_{{info['expiration']}}"

        opt = Option(info['symbol'], info['expiration'], info['strike'], 'P', 'SMART')
        qualified = ib.qualifyContracts(opt)

        if qualified:
            ticker = ib.reqMktData(qualified[0], '106', False, False)
            ib.sleep(2)

            data = {{}}
            if ticker.bid and ticker.bid > 0:
                data['bid'] = ticker.bid
            if ticker.ask and ticker.ask > 0:
                data['ask'] = ticker.ask
            if data.get('bid') and data.get('ask'):
                data['mid'] = (data['bid'] + data['ask']) / 2

            if ticker.modelGreeks:
                g = ticker.modelGreeks
                data['delta'] = g.delta
                data['theta'] = g.theta
                data['gamma'] = g.gamma
                data['vega'] = g.vega
                data['iv'] = g.impliedVol

            ib.cancelMktData(qualified[0])

            # Get margin for this position using whatIfOrder
            try:
                order = MarketOrder("BUY", 1)  # Simulate closing 1 contract
                whatif = ib.whatIfOrder(qualified[0], order)
                if whatif and whatif.maintMarginChange:
                    maint_change = float(whatif.maintMarginChange)
                    # Negative change means margin would be released (currently used)
                    data['margin'] = -maint_change if maint_change < 0 else 0
            except:
                pass

            result[key] = data

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
    """Get TWS connection status and live orders."""
    import asyncio

    # Use socket-based check to avoid ib_insync event loop issues
    result = await asyncio.to_thread(_check_connection_via_socket)

    # If connected, try to get detailed info using ib_insync in subprocess
    if result["connection"]["connected"]:
        try:
            import subprocess
            import json
            from ibkr_spy_puts.config import TWSSettings

            tws_settings = TWSSettings()
            # Run a quick subprocess to get account info, orders, and positions
            script = f'''
import json
import asyncio
asyncio.set_event_loop(asyncio.new_event_loop())
from ib_insync import IB
ib = IB()
result = {{"account": None, "trading_mode": None, "orders": [], "positions": []}}
try:
    ib.connect("{tws_settings.host}", {tws_settings.port}, clientId=98, readonly=True, timeout=10)
    accounts = ib.managedAccounts()
    if accounts:
        result["account"] = accounts[0]
        result["trading_mode"] = "PAPER" if accounts[0].startswith("DU") else "LIVE"

    # Get open orders
    ib.reqAllOpenOrders()
    ib.sleep(1)
    for trade in ib.openTrades():
        c, o, s = trade.contract, trade.order, trade.orderStatus
        result["orders"].append({{
            "symbol": c.symbol,
            "sec_type": c.secType,
            "strike": getattr(c, "strike", None),
            "expiration": getattr(c, "lastTradeDateOrContractMonth", None),
            "right": getattr(c, "right", None),
            "action": o.action,
            "order_type": o.orderType,
            "quantity": int(o.totalQuantity),
            "limit_price": o.lmtPrice if o.lmtPrice else None,
            "stop_price": o.auxPrice if o.auxPrice else None,
            "status": s.status,
            "filled": int(s.filled),
            "remaining": int(s.remaining),
            "oca_group": o.ocaGroup if o.ocaGroup else None,
        }})

    # Get live positions
    for pos in ib.positions():
        c = pos.contract
        if c.secType == "OPT":
            result["positions"].append({{
                "symbol": c.symbol,
                "strike": c.strike,
                "expiration": c.lastTradeDateOrContractMonth,
                "right": c.right,
                "quantity": int(pos.position),
                "avg_cost": pos.avgCost,
            }})

    ib.disconnect()
except Exception as e:
    result["error"] = str(e)
print(json.dumps(result))
'''
            proc = await asyncio.to_thread(
                subprocess.run,
                ["python", "-c", script],
                capture_output=True,
                text=True,
                timeout=20,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                data = json.loads(proc.stdout.strip())
                if data.get("account"):
                    result["connection"]["account"] = data["account"]
                    result["connection"]["trading_mode"] = data.get("trading_mode")
                    result["connection"]["logged_in"] = True
                    result["connection"]["ready_to_trade"] = True
                result["live_orders"] = data.get("orders", [])
                result["ibkr_positions"] = data.get("positions", [])
        except Exception as e:
            # Fall back to socket-only result
            result["connection"]["error"] = str(e)

    return result


@app.get("/api/connection-status")
async def api_connection_status():
    """Check TWS/Gateway connection status and trading readiness."""
    result = await get_connection_and_orders()
    return result["connection"]


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
