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

        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "positions": positions,
                "summary": summary,
                "risk": risk,
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
