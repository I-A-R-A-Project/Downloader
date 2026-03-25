import argparse, sys
from PyQt5.QtWidgets import QApplication

from mod_search.window import ModSearchWindow

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--game", default="factorio")
    args = parser.parse_args()

    app = QApplication(sys.argv)
    window = ModSearchWindow(game=args.game)
    window.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    sys.exit(main())
