import sys
from PyQt5.QtWidgets import QApplication

from media_search.window import MediaSearchUI


def main():
    app = QApplication(sys.argv)
    window = MediaSearchUI()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
