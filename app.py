from pathlib import Path
import logging
import sys
from PySide6.QtWidgets import QApplication
from ui.main_window import MainWindow
from ui.theme import apply_palette

def main():
    root=Path(__file__).resolve().parent;(root/"logs").mkdir(exist_ok=True)
    logging.basicConfig(filename=root/"logs/app.log",level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
    app=QApplication(sys.argv)
    app.setApplicationName("动平衡测试系统")
    app.setOrganizationName("Boreasa")
    apply_palette(app)
    w=MainWindow(root)
    w.show()
    return app.exec()
if __name__=="__main__":raise SystemExit(main())
