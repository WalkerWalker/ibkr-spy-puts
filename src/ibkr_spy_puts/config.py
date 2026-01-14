"""Configuration management using Pydantic Settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class TWSSettings(BaseSettings):
    """TWS/IB Gateway connection settings."""

    model_config = SettingsConfigDict(env_prefix="TWS_")

    host: str = "127.0.0.1"
    port: int = 7496  # Live: 7496, Paper: 7497
    client_id: int = 1


class DatabaseSettings(BaseSettings):
    """PostgreSQL database settings."""

    model_config = SettingsConfigDict(env_prefix="DB_")

    host: str = "localhost"
    port: int = 5432
    name: str = "ibkr_puts"
    user: str = "postgres"
    password: str = ""

    @property
    def connection_string(self) -> str:
        """Generate PostgreSQL connection string."""
        return f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{self.name}"


class StrategySettings(BaseSettings):
    """Put selling strategy settings."""

    model_config = SettingsConfigDict(env_prefix="STRATEGY_")

    symbol: str = "SPY"
    quantity: int = 1
    order_type: str = "LMT"  # LMT or MKT
    limit_offset: float = 0.05

    # Option selection criteria
    target_dte: int = 90  # Target days to expiration (closest to this)
    target_delta: float = -0.15  # Target delta (closest to this, negative for puts)

    # Legacy settings (kept for backwards compatibility)
    days_to_expiration: int = 90  # Alias for target_dte
    strike_offset_pct: float = 2.0  # % below current price (alternative to delta)


class BracketSettings(BaseSettings):
    """Bracket order settings for automatic profit taking and stop loss."""

    model_config = SettingsConfigDict(env_prefix="BRACKET_")

    enabled: bool = True  # Enable/disable automatic bracket orders

    # Take profit: % of premium to capture before closing
    # 60% means buy back at 40% of original premium (e.g., sold for $1, buy back at $0.40)
    take_profit_pct: float = 60.0

    # Stop loss: % of premium loss before closing
    # 200% means buy back at 300% of original (e.g., sold for $1, buy back at $3, losing $2)
    stop_loss_pct: float = 200.0


class ScheduleSettings(BaseSettings):
    """Trading schedule settings."""

    model_config = SettingsConfigDict(env_prefix="SCHEDULE_")

    trade_at_open: bool = True  # Trade at market open
    trade_time: str = "09:30"  # ET - market open time (used if trade_at_open is True)
    timezone: str = "America/New_York"


class Settings(BaseSettings):
    """Main application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    tws: TWSSettings = TWSSettings()
    database: DatabaseSettings = DatabaseSettings()
    strategy: StrategySettings = StrategySettings()
    bracket: BracketSettings = BracketSettings()
    schedule: ScheduleSettings = ScheduleSettings()


def get_settings() -> Settings:
    """Get application settings."""
    return Settings()
