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
from fastapi.staticfiles import StaticFiles
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
    """Get all open positions with latest data."""
    db = get_db()
    try:
        positions = db.get_open_positions()
        return serialize_decimal(positions)
    finally:
        db.disconnect()


@app.get("/api/summary")
async def get_summary():
    """Get strategy summary metrics."""
    db = get_db()
    try:
        summary = db.get_strategy_summary()
        return serialize_decimal(summary)
    finally:
        db.disconnect()


@app.get("/api/risk")
async def get_risk():
    """Get risk metrics for all open positions."""
    db = get_db()
    try:
        risk = db.get_risk_metrics()
        return serialize_decimal(risk)
    finally:
        db.disconnect()


@app.get("/api/trades")
async def get_trades(status: str = None):
    """Get all trades, optionally filtered by status."""
    db = get_db()
    try:
        if status == "open":
            trades = db.get_open_trades()
            return serialize_decimal([{
                "id": t.id,
                "trade_date": t.trade_date,
                "symbol": t.symbol,
                "strike": t.strike,
                "expiration": t.expiration,
                "quantity": t.quantity,
                "entry_price": t.entry_price,
                "expected_tp_price": t.expected_tp_price,
                "expected_sl_price": t.expected_sl_price,
                "status": t.status,
            } for t in trades])
        else:
            # Return all trades via positions view for open, direct query for closed
            positions = db.get_open_positions()
            return serialize_decimal(positions)
    finally:
        db.disconnect()


@app.get("/api/trades/{trade_id}/orders")
async def get_trade_orders(trade_id: int):
    """Get all orders for a specific trade."""
    db = get_db()
    try:
        orders = db.get_orders_for_trade(trade_id)
        return serialize_decimal([{
            "id": o.id,
            "order_type": o.order_type,
            "action": o.action,
            "limit_price": o.limit_price,
            "stop_price": o.stop_price,
            "fill_price": o.fill_price,
            "status": o.status,
            "ibkr_order_id": o.ibkr_order_id,
        } for o in orders])
    finally:
        db.disconnect()


@app.get("/api/pnl/monthly")
async def get_monthly_pnl():
    """Get P&L aggregated by month."""
    db = get_db()
    try:
        pnl = db.get_pnl_by_month()
        return serialize_decimal(pnl)
    finally:
        db.disconnect()


@app.get("/api/pending-orders")
async def get_pending_orders():
    """Get all pending orders."""
    db = get_db()
    try:
        orders = db.get_pending_orders()
        return serialize_decimal(orders)
    finally:
        db.disconnect()


# =============================================================================
# Connection Status & Live Orders
# =============================================================================


def get_connection_and_orders():
    """Get TWS connection status and live orders in one connection."""
    from ibkr_spy_puts.config import TWSSettings, ScheduleSettings
    from ibkr_spy_puts.scheduler import MarketCalendar
    from ib_insync import IB
    import os

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
    }

    ib = IB()
    try:
        ib.connect(tws_settings.host, tws_settings.port, clientId=99, readonly=True)
        result["connection"]["connected"] = True

        # Get account info
        accounts = ib.managedAccounts()
        if accounts:
            result["connection"]["logged_in"] = True
            result["connection"]["account"] = accounts[0]
            if accounts[0].startswith("DU"):
                result["connection"]["trading_mode"] = "PAPER"
            else:
                result["connection"]["trading_mode"] = "LIVE"

        # Check if today is a trading day
        calendar = MarketCalendar()
        result["connection"]["is_trading_day"] = calendar.is_trading_day()
        result["connection"]["ready_to_trade"] = result["connection"]["connected"] and result["connection"]["logged_in"]

        # Get all open orders
        ib.reqAllOpenOrders()
        ib.sleep(2)

        for trade in ib.openTrades():
            contract = trade.contract
            order = trade.order
            status = trade.orderStatus

            order_info = {
                "order_id": order.orderId,
                "symbol": contract.symbol,
                "sec_type": contract.secType,
                "strike": getattr(contract, 'strike', None),
                "expiration": getattr(contract, 'lastTradeDateOrContractMonth', None),
                "right": getattr(contract, 'right', None),
                "action": order.action,
                "order_type": order.orderType,
                "quantity": int(order.totalQuantity),
                "limit_price": order.lmtPrice if order.lmtPrice else None,
                "stop_price": order.auxPrice if order.auxPrice else None,
                "status": status.status,
                "filled": int(status.filled),
                "remaining": int(status.remaining),
                "parent_id": order.parentId if order.parentId else None,
            }
            result["live_orders"].append(order_info)

        ib.disconnect()

    except Exception as e:
        result["connection"]["error"] = str(e)

    return result


@app.get("/api/connection-status")
async def get_connection_status():
    """Check TWS/Gateway connection status and trading readiness."""
    result = get_connection_and_orders()
    return result["connection"]


@app.get("/api/live-orders")
async def get_live_orders():
    """Get all live orders from IBKR."""
    result = get_connection_and_orders()
    return {"orders": result["live_orders"], "connected": result["connection"]["connected"]}


# =============================================================================
# Greeks Refresh
# =============================================================================


@app.get("/refresh-greeks")
async def refresh_greeks():
    """Fetch live Greeks from TWS and update position snapshots."""
    from fastapi.responses import RedirectResponse

    try:
        from ib_insync import IB, Option
        from ibkr_spy_puts.config import TWSSettings

        tws_settings = TWSSettings()
        ib = IB()
        ib.connect(tws_settings.host, tws_settings.port, clientId=99)  # Use different clientId

        db = get_db()
        positions = db.get_open_positions()

        for pos in positions:
            # Create contract
            contract = Option('SPY', pos['expiration'].strftime('%Y%m%d'), float(pos['strike']), 'P', 'SMART')
            ib.qualifyContracts(contract)

            # Request market data with Greeks
            ib.reqMktData(contract, genericTickList='106', snapshot=False)
            ib.sleep(2)

            ticker = ib.ticker(contract)
            if ticker.modelGreeks:
                greeks = ticker.modelGreeks
                mid_price = (ticker.bid + ticker.ask) / 2 if ticker.bid and ticker.ask else ticker.last
                unrealized_pnl = (float(pos['entry_price']) - mid_price) * 100

                # Insert snapshot
                db.cursor.execute("""
                    INSERT INTO position_snapshots
                    (trade_id, current_mid, unrealized_pnl, delta, theta, gamma, vega, iv, days_to_expiry)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    pos['id'], mid_price, unrealized_pnl,
                    greeks.delta, greeks.theta, greeks.gamma, greeks.vega,
                    greeks.impliedVol, pos['days_to_expiry']
                ))
                db.conn.commit()

            ib.cancelMktData(contract)

        ib.disconnect()
        db.disconnect()

    except Exception as e:
        print(f"Error refreshing Greeks: {e}")

    return RedirectResponse(url="/", status_code=303)


# =============================================================================
# Dashboard Pages
# =============================================================================


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Main dashboard page."""
    db = get_db()
    try:
        positions = db.get_open_positions()
        summary = db.get_strategy_summary()
        risk = db.get_risk_metrics()

        # Get connection status and live orders in one call
        ibkr_data = get_connection_and_orders()

        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "positions": positions,
                "summary": summary,
                "risk": risk,
                "connection": ibkr_data["connection"],
                "live_orders": ibkr_data["live_orders"],
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
