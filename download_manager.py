import os, sys, json
from PyQt5.QtWidgets import QApplication
from ui import LinkInputWindow, DownloadWindow

def parse_input(args):
    if len(args) == 1 and isinstance(args[0], str) and args[0].endswith(".json"):
        with open(args[0], "r", encoding="utf-8") as f:
            data = json.load(f)
        return data
    else:
        return [{"url": a, "path": ""} for a in args]

if __name__ == '__main__':
    os.system("title Descargas")
    app = QApplication(sys.argv)
    args = sys.argv[1:]

    if not args:
        link_input = LinkInputWindow()
        link_input.show()
        app.exec_()
        args = link_input.links
    else:
        args = parse_input(args)

    if args:
        window = DownloadWindow(args)
        sys.exit(app.exec_())