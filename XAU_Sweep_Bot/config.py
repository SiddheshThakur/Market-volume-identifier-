import os
from dotenv import load_dotenv

load_dotenv()

# Telegram Bot Configuration
# Step 3: Create Telegram Bot (Free) via @BotFather
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")

# --- Trading Strategy Configuration ---
SYMBOL = "XAUUSD"

# Session Times (Broker Server Time - typically EET/EEST)
# HH:MM format
ASIA_START = "00:00"
ASIA_END = "08:00"

LONDON_START = "08:00"
LONDON_END = "16:00"

NY_START = "13:00"
NY_END = "21:00"

# Strategy Thresholds
VOLUME_SPIKE_MULTIPLIER = 2.5
ATR_PERIOD = 14
DISPLACEMENT_MULTIPLIER = 1.5
