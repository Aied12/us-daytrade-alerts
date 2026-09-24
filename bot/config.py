from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

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
    telegram_channel_id: str = ""
    telegram_force_off: bool = False  # TELEGRAM_ENABLED=0 or data/telegram_paused
    extra_chat_ids: list[str] = field(default_factory=list)  # 89 multi-user
    min_price_usd: float = 5.0
    urgent_min_score: float = 5.0
    user_mode: str = "beginner"
    timezone_name: str = "Asia/Riyadh"  # 93
    light_mode: bool = False  # 95
    api_thrift: bool = False  # 85
    dashboard_token: str = ""  # 90 simple web auth
    finnhub_api_key: str = ""  # near-real-time quotes (free)
    alpaca_api_key: str = ""
    alpaca_api_secret: str = ""
    watchlist: list[str] = field(default_factory=lambda: list(DEFAULT_WATCHLIST))
    data_dir: Path = ROOT / "data"
    logs_dir: Path = ROOT / "logs"

    @property
    def has_live_quotes(self) -> bool:
        if self.finnhub_api_key:
            return True
        return bool(self.alpaca_api_key and self.alpaca_api_secret)

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
    def telegram_paused(self) -> bool:
        if self.telegram_force_off:
            return True
        return (self.data_dir / "telegram_paused").exists()

    @property
    def telegram_enabled(self) -> bool:
        if self.telegram_paused:
            return False
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    @property
    def is_beginner(self) -> bool:
        return self.user_mode.lower() != "pro"

    @property
    def local_tz(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone_name)
        except Exception:
            return ZoneInfo("Asia/Riyadh")

    @property
    def all_private_chat_ids(self) -> list[str]:
        ids = []
        if self.telegram_chat_id:
            ids.append(self.telegram_chat_id)
        for cid in self.extra_chat_ids:
            if cid and cid not in ids:
                ids.append(cid)
        return ids


def load_settings() -> Settings:
    watchlist_raw = os.getenv("WATCHLIST", "")
    watchlist = (
        [s.strip().upper() for s in watchlist_raw.split(",") if s.strip()]
        if watchlist_raw
        else list(DEFAULT_WATCHLIST)
    )
    extras = [
        x.strip()
        for x in os.getenv("TELEGRAM_EXTRA_CHAT_IDS", "").split(",")
        if x.strip()
    ]
    mode = os.getenv("USER_MODE", "beginner").strip().lower()
    if mode not in ("beginner", "pro"):
        mode = "beginner"
    light = os.getenv("LIGHT_MODE", "0").strip().lower() in ("1", "true", "yes")
    thrift = os.getenv("API_THRIFT", "1" if light else "0").strip().lower() in (
        "1", "true", "yes"
    )
    tg_flag = os.getenv("TELEGRAM_ENABLED", "1").strip().lower()
    telegram_force_off = tg_flag in ("0", "false", "no", "off")
    settings = Settings(
        capital_sar=float(os.getenv("CAPITAL_SAR", "45000")),
        usd_sar_rate=float(os.getenv("USD_SAR_RATE", "3.75")),
        risk_per_trade=float(os.getenv("RISK_PER_TRADE", "0.01")),
        daily_loss_limit=float(os.getenv("DAILY_LOSS_LIMIT", "0.02")),
        max_alerts_per_day=int(os.getenv("MAX_ALERTS_PER_DAY", "5")),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip(),
        telegram_channel_id=os.getenv("TELEGRAM_CHANNEL_ID", "").strip(),
        telegram_force_off=telegram_force_off,
        extra_chat_ids=extras,
        min_price_usd=float(os.getenv("MIN_PRICE_USD", "5")),
        urgent_min_score=float(os.getenv("URGENT_MIN_SCORE", "5")),
        user_mode=mode,
        timezone_name=os.getenv("TIMEZONE", "Asia/Riyadh").strip() or "Asia/Riyadh",
        light_mode=light,
        api_thrift=thrift,
        dashboard_token=os.getenv("DASHBOARD_TOKEN", "").strip(),
        finnhub_api_key=os.getenv("FINNHUB_API_KEY", "").strip(),
        alpaca_api_key=os.getenv("ALPACA_API_KEY", "").strip(),
        alpaca_api_secret=os.getenv("ALPACA_API_SECRET", "").strip(),
        watchlist=watchlist,
    )
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "media").mkdir(parents=True, exist_ok=True)
    return settings
