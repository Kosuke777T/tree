# -*- coding: utf-8 -*-
"""
メインGUI。起動時に ZIP 自動展開を実行してからウィンドウを表示する。
"""

from pathlib import Path

# GUI 生成の一番最初に ZIP 展開を1回だけ実行
from app.services.zip_extract_service import (
    FARM_DIR,
    ZIP_DIR,
    extract_all_zips_on_start,
)


def _run_gui() -> None:
    """GUI のメインループ（将来: 家系図・DB表表示など）。"""
    import tkinter as tk
    root = tk.Tk()
    root.title("Tree")
    root.geometry("800x600")
    # 将来ここで家系図やDB表を表示
    label = tk.Label(root, text="ZIP 展開済み。家系図・DB表示は今後追加予定です。", font=("", 12))
    label.pack(expand=True)
    root.mainloop()


def main() -> None:
    # 初期化の一番最初: zip を farm へ自動展開（新しいもののみ）
    extract_all_zips_on_start(zip_dir=ZIP_DIR, farm_dir=FARM_DIR)
    _run_gui()


if __name__ == "__main__":
    main()
