import os, sys
from PyQt5.QtWidgets import QApplication
from ui import LinkInputWindow, DownloadWindow

if __name__ == '__main__':
    os.system("title Descargas")
    app = QApplication(sys.argv)
    args = sys.argv[1:]
    if not args:
        link_input = LinkInputWindow()
        link_input.show()
        app.exec_()
        args = link_input.links
    if args:
        window = DownloadWindow(args)
        sys.exit(app.exec_())