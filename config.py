import os
from pathlib import Path
from dotenv import load_dotenv

# Base directory
BASE_DIR = Path(__file__).resolve().parent

# Load environment variables
load_dotenv(BASE_DIR / ".env")

class Config:
    # Telegram Userbot credentials
    TELEGRAM_API_ID: int = int(os.getenv("TELEGRAM_API_ID", "0"))
    TELEGRAM_API_HASH: str = os.getenv("TELEGRAM_API_HASH", "")
    
    # Telegram BotFather token
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    ADMIN_TELEGRAM_ID: int = int(os.getenv("ADMIN_TELEGRAM_ID", "0"))
    
    # Groq API Key
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    GROQ_MODEL: str = "groq/compound"
    
    # Google Gemini credentials
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
    
    # MetaTrader 5 parameters
    MT5_ACCOUNT: int = int(os.getenv("MT5_ACCOUNT", "0"))
    MT5_PASSWORD: str = os.getenv("MT5_PASSWORD", "")
    MT5_SERVER: str = os.getenv("MT5_SERVER", "Exness-MT5Trial")
    MT5_SYMBOL: str = os.getenv("MT5_SYMBOL", "XAUUSDm")
    
    # Trading Rules & Risk Management (Strict Guardrail: 1.0% - 5.0%)
    MIN_RISK_PERCENT: float = 1.0
    MAX_RISK_PERCENT: float = 5.0
    RISK_PER_TRADE_PERCENT: float = 1.0   # Default 1.0% risk per trade
    TARGET_RR: float = 3.0                # 1:3 RR ratio
    BE_TRIGGER_R: float = 1.0             # Move to Break Even at +1R
    MAX_SL_PIPS: float = 50.0             # Max allowed SL distance (50 pips = 5.0 points in XAUUSD)
    MAX_DAILY_DRAWDOWN_PERCENT: float = 5.0  # 5% max daily drawdown stop
    CONFIDENCE_THRESHOLD: float = 66.0   # Minimum 66% consensus score to open trade
    
    # Default Source Weights for Scoring
    WEIGHT_USER: float = 3.0
    WEIGHT_CHANNEL_DEFAULT: float = 1.5
    
    # News Filter
    NEWS_PAUSE_MINUTES_BEFORE: int = 15
    NEWS_PAUSE_MINUTES_AFTER: int = 15
    
    # Database
    DB_PATH: str = str(BASE_DIR / "data" / "trading_system.db")
    EXCEL_EXPORT_PATH: str = str(BASE_DIR / "data" / "xatolar.xlsx")

    # Tunnel Provider (ngrok or cloudflared)
    TUNNEL_PROVIDER: str = os.getenv("TUNNEL_PROVIDER", "ngrok")
    NGROK_DOMAIN: str = os.getenv("NGROK_DOMAIN", "")
    NGROK_AUTHTOKEN: str = os.getenv("NGROK_AUTHTOKEN", "")

    # WebApp & API Server
    WEBAPP_HOST: str = os.getenv("WEBAPP_HOST", "0.0.0.0")
    WEBAPP_PORT: int = int(os.getenv("WEBAPP_PORT", "8000"))


    @property
    def WEBAPP_URL(self) -> str:
        # Check active environment first
        env_val = os.getenv("WEBAPP_URL")
        # Check .env file directly for live tunnel updates
        env_file = BASE_DIR / ".env"
        if env_file.exists():
            try:
                for line in env_file.read_text().splitlines():
                    if line.strip().startswith("WEBAPP_URL="):
                        val = line.split("=", 1)[1].strip()
                        if val:
                            return val
            except Exception:
                pass
        return env_val or "http://localhost:8000"


config = Config()
