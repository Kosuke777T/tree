"""Entry point for the tree application."""

from __future__ import annotations

import sys

from PyQt6.QtWidgets import QApplication

from app.gui.main_window import MainWindow


def main() -> None:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
