# Telegram Bot Configuration
# Step 3: Create Telegram Bot (Free) via @BotFather
BOT_TOKEN = "your_token_here"
CHAT_ID = "your_chat_id_here"

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
