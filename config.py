
import os
BOT_TOKEN           = os.environ.get("BOT_TOKEN", "YOUR_TOKEN")
SUPERADMIN_ID       = int(os.environ.get("SUPERADMIN_ID", "0"))
NOTIFICATIONS_CHAT_ID = int(os.environ.get("NOTIFICATIONS_CHAT_ID", "0"))
REPORTS_CHAT_ID     = int(os.environ.get("REPORTS_CHAT_ID", "0"))
TIMEZONE            = os.environ.get("TIMEZONE", "Europe/Moscow")
