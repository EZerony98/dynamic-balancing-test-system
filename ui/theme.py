from __future__ import annotations

from PySide6.QtGui import QColor, QPalette


RED = "#d5122d"
RED_DARK = "#ad0d24"
RED_SOFT = "#fff1f3"
TEXT = "#20242b"
MUTED = "#667085"
BORDER = "#e4e7ec"
SURFACE = "#ffffff"
CANVAS = "#f5f6f8"
GREEN = "#138a5b"
AMBER = "#b76e00"


APP_STYLE = f"""
QMainWindow, QWidget {{
    background: {CANVAS};
    color: {TEXT};
    font-family: "PingFang SC", "Microsoft YaHei", sans-serif;
    font-size: 14px;
}}
QFrame#Header, QFrame#Toolbar, QFrame#Card, QFrame#Panel {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 10px;
}}
QFrame#Header {{ border-radius: 0; border-left: 0; border-right: 0; border-top: 0; }}
QLabel#AppTitle {{ font-size: 21px; font-weight: 700; color: #111318; }}
QLabel#PageTitle {{ font-size: 22px; font-weight: 700; color: #111318; }}
QLabel#SectionTitle {{ font-size: 16px; font-weight: 650; color: #20242b; }}
QLabel#MetricValue {{ font-size: 28px; font-weight: 750; color: #111318; }}
QLabel#MetricUnit {{ color: {MUTED}; font-size: 13px; }}
QLabel#Hint {{ color: {MUTED}; }}
QLabel#Danger {{ color: {RED}; font-weight: 650; }}
QLabel#Success {{ color: {GREEN}; font-weight: 650; }}
QListWidget#Navigation {{
    background: {SURFACE}; border: 0; border-right: 1px solid {BORDER};
    padding: 14px 10px; outline: none;
}}
QListWidget#Navigation::item {{
    color: #4a4f59; padding: 13px 14px; margin: 2px 0; border-radius: 7px;
}}
QListWidget#Navigation::item:hover {{ background: #f7f0f1; color: {RED}; }}
QListWidget#Navigation::item:selected {{
    background: {RED_SOFT}; color: {RED}; font-weight: 650;
    border-left: 3px solid {RED};
}}
QPushButton {{
    background: {SURFACE}; color: #353942; border: 1px solid #cfd4dc;
    border-radius: 7px; padding: 8px 15px; min-height: 20px;
}}
QPushButton:hover {{ border-color: {RED}; color: {RED}; background: #fffafa; }}
QPushButton:pressed {{ background: {RED_SOFT}; }}
QPushButton:disabled {{ color: #a8adb5; background: #f2f3f5; border-color: #e2e4e8; }}
QPushButton#Primary {{ background: {RED}; color: white; border-color: {RED}; font-weight: 650; }}
QPushButton#Primary:hover {{ background: {RED_DARK}; border-color: {RED_DARK}; color: white; }}
QPushButton#DangerButton {{ color: {RED}; border-color: #efacb7; }}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit {{
    background: white; border: 1px solid #cfd4dc; border-radius: 6px;
    padding: 6px 9px; min-height: 22px; selection-background-color: {RED};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border: 1px solid {RED}; }}
QComboBox::drop-down {{ border: 0; width: 24px; }}
QTableWidget {{
    background: white; alternate-background-color: #fbfbfc; border: 1px solid {BORDER};
    border-radius: 8px; gridline-color: #edf0f3; selection-background-color: {RED_SOFT};
    selection-color: {TEXT};
}}
QHeaderView::section {{
    background: #f7f8fa; color: #475467; padding: 8px; border: 0;
    border-bottom: 1px solid {BORDER}; font-weight: 650;
}}
QTabWidget::pane {{ background: white; border: 1px solid {BORDER}; border-radius: 8px; }}
QTabBar::tab {{ padding: 9px 16px; color: {MUTED}; }}
QTabBar::tab:selected {{ color: {RED}; border-bottom: 2px solid {RED}; font-weight: 650; }}
QPlainTextEdit {{ background: white; border: 1px solid {BORDER}; border-radius: 8px; padding: 8px; }}
QStatusBar {{ background: white; color: {MUTED}; border-top: 1px solid {BORDER}; }}
QScrollArea {{ border: 0; background: transparent; }}
QToolTip {{ background: #20242b; color: white; border: 0; padding: 5px; }}
"""


def apply_palette(app) -> None:
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(CANVAS))
    palette.setColor(QPalette.ColorRole.Base, QColor(SURFACE))
    palette.setColor(QPalette.ColorRole.Text, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Button, QColor(SURFACE))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(RED))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    app.setPalette(palette)
    app.setStyleSheet(APP_STYLE)
