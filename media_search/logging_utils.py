import logging
import os
from logging.handlers import RotatingFileHandler

from config import CONFIG_PATH


LOG_DIR = os.path.join(os.path.dirname(CONFIG_PATH), "logs")
LOG_PATH = os.path.join(LOG_DIR, "media_search.log")


def configure_media_search_logging():
    logger = logging.getLogger("media_search")
    if logger.handlers:
        return logger

    os.makedirs(LOG_DIR, exist_ok=True)

    handler = RotatingFileHandler(
        LOG_PATH,
        maxBytes=1_500_000,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    logger.propagate = False
    logger.info("Logging initialized")
    return logger
