import logging
from pathlib import Path

APP_DIR = Path.home() / "AppData" / "Local" / "GoldTracker"
APP_DIR.mkdir(parents=True, exist_ok=True)

LOG_FILE = APP_DIR / "app.log"

logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("GoldTracker")