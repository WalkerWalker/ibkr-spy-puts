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


@app.get("/api/positions/closed")
async def get_closed_positions(limit: int = 50):
    """Get closed positions with P&L."""
    db = get_db()
    try:
        positions = db.get_closed_positions_for_display(limit=limit)
        return serialize_decimal(positions)
    finally:
        db.disconnect()


@app.get("/api/positions/live")
async def get_positions_live():
    """Get all open positions enriched with live IBKR data.

    Returns positions with:
    - Current price, bid, ask
    - Greeks (delta, theta, gamma, vega, IV)
    - Margin per position
    - Unrealized P&L
    - Days to expiry, days in trade

    Data comes from the connection manager's streaming cache.
    """
    from ibkr_spy_puts.scheduler import MarketCalendar

    manager = get_connection_manager()
    calendar = MarketCalendar()

    return {
        "positions": manager.get_positions(),
        "spy_price": manager.get_spy_price(),
        "data_source": "live",
        "market_open": calendar.is_market_open(),
    }


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
    Uses the persistent connection manager's streaming subscription.
    """
    manager = get_connection_manager()
    return manager.get_spy_price()


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


# =============================================================================
# Dashboard Pages
# =============================================================================


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Main dashboard page."""
    db = get_db()
    try:
        positions = db.get_positions_for_display()
        closed_positions = db.get_closed_positions_for_display(limit=50)
        summary = db.get_strategy_summary()
        trade_history = db.get_trade_history()

        # Get connection status and live orders in one call
        ibkr_data = await get_connection_and_orders()

        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "positions": positions,
                "closed_positions": closed_positions,
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


