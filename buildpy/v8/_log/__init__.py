import logging
import os

logger = logging.getLogger(__name__)
logger.setLevel(getattr(logging, os.environ.get("BUILDPY_LOG_LEVEL", "debug").upper()))
