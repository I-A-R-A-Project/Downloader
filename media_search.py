import sys
from PyQt5.QtWidgets import QApplication

from media_search.logging_utils import configure_media_search_logging
from media_search.window import MediaSearchUI


def main():
    logger = configure_media_search_logging()
    logger.info("Starting media_search UI")
    sys.excepthook = lambda exc_type, exc_value, exc_traceback: logger.exception(
        "Unhandled exception",
        exc_info=(exc_type, exc_value, exc_traceback),
    )
    app = QApplication(sys.argv)
    window = MediaSearchUI()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
