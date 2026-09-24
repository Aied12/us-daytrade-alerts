from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env", override=True)

DEFAULT_WATCHLIST = [
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA", "AMD",
    "NFLX", "AVGO", "CRM", "COST", "JPM", "BAC", "XOM", "CVX",
    "SPY", "QQQ", "IWM", "DIS", "BA", "UBER", "COIN", "PLTR",
]


@dataclass
class Settings:
    capital_sar: float = 45_000.0
    usd_sar_rate: float = 3.75
    risk_per_trade: float = 0.01
    daily_loss_limit: float = 0.02
    max_alerts_per_day: int = 5
    max_morning_picks: int = 7
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_channel_id: str = ""  # public channel (optional)
    min_price_usd: float = 5.0
    urgent_min_score: float = 5.0
    user_mode: str = "beginner"  # beginner | pro
    watchlist: list[str] = field(default_factory=lambda: list(DEFAULT_WATCHLIST))
    data_dir: Path = ROOT / "data"
    logs_dir: Path = ROOT / "logs"

    @property
    def capital_usd(self) -> float:
        return self.capital_sar / self.usd_sar_rate

    @property
    def risk_budget_sar(self) -> float:
        return self.capital_sar * self.risk_per_trade

    @property
    def risk_budget_usd(self) -> float:
        return self.risk_budget_sar / self.usd_sar_rate

    @property
    def daily_loss_budget_sar(self) -> float:
        return self.capital_sar * self.daily_loss_limit

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    @property
    def is_beginner(self) -> bool:
        return self.user_mode.lower() != "pro"


def load_settings() -> Settings:
    watchlist_raw = os.getenv("WATCHLIST", "")
    watchlist = (
        [s.strip().upper() for s in watchlist_raw.split(",") if s.strip()]
        if watchlist_raw
        else list(DEFAULT_WATCHLIST)
    )
    mode = os.getenv("USER_MODE", "beginner").strip().lower()
    if mode not in ("beginner", "pro"):
        mode = "beginner"
    settings = Settings(
        capital_sar=float(os.getenv("CAPITAL_SAR", "45000")),
        usd_sar_rate=float(os.getenv("USD_SAR_RATE", "3.75")),
        risk_per_trade=float(os.getenv("RISK_PER_TRADE", "0.01")),
        daily_loss_limit=float(os.getenv("DAILY_LOSS_LIMIT", "0.02")),
        max_alerts_per_day=int(os.getenv("MAX_ALERTS_PER_DAY", "5")),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip(),
        telegram_channel_id=os.getenv("TELEGRAM_CHANNEL_ID", "").strip(),
        min_price_usd=float(os.getenv("MIN_PRICE_USD", "5")),
        urgent_min_score=float(os.getenv("URGENT_MIN_SCORE", "5")),
        user_mode=mode,
        watchlist=watchlist,
    )
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "media").mkdir(parents=True, exist_ok=True)
    return settings
