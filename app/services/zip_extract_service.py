# -*- coding: utf-8 -*-
"""
ZIP自動展開サービス。
GUI起動時に zip フォルダ内の *.zip を farm 直下へ展開する。
前回より新しい zip のみ展開し、状態は state JSON に保持する。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import zipfile
from pathlib import Path
from typing import Any

# デフォルトの対象フォルダ（raw string / Path で扱う）
ZIP_DIR = r"D:\tree\zip"
FARM_DIR = r"D:\tree\farm"

# 状態ファイル・ログファイル名（farm 配下）
STATE_FILENAME = ".zip_extract_state.json"
STATE_BROKEN_BACKUP = ".zip_extract_state.json.broken.json"
LOG_FILENAME = "zip_extract.log"


def _ensure_farm_dir(farm_dir: Path) -> None:
    """farm ディレクトリが存在することを保証する。"""
    farm_dir.mkdir(parents=True, exist_ok=True)


def _log_path(farm_dir: Path) -> Path:
    return farm_dir / LOG_FILENAME


def _state_path(farm_dir: Path) -> Path:
    return farm_dir / STATE_FILENAME


def _setup_logger(farm_dir: Path) -> logging.Logger:
    """farm 配下の zip_extract.log に UTF-8 で出力するロガーを用意する。"""
    _ensure_farm_dir(farm_dir)
    log_path = _log_path(farm_dir)
    logger = logging.getLogger("zip_extract")
    logger.setLevel(logging.DEBUG)
    # 既存ハンドラを消してから追加（二重登録防止）
    logger.handlers.clear()
    handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(handler)
    return logger


def _load_state(farm_dir: Path, logger: logging.Logger) -> dict[str, Any]:
    """
    状態 JSON を読み込む。壊れていれば .broken.json に退避して空の状態で返す。
    """
    state_path = _state_path(farm_dir)
    if not state_path.exists():
        return {"zips": {}}

    try:
        with open(state_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data.get("zips"), dict):
            data = {"zips": {}}
        return data
    except (json.JSONDecodeError, OSError) as e:
        broken_path = farm_dir / STATE_BROKEN_BACKUP
        try:
            if state_path.exists():
                state_path.rename(broken_path)
                logger.warning("state JSON が壊れていたため %s に退避して初期化しました: %s", broken_path, e)
        except OSError:
            pass
        return {"zips": {}}


def _save_state(farm_dir: Path, state: dict[str, Any], logger: logging.Logger) -> None:
    """状態 JSON を UTF-8 で書き込む。"""
    state_path = _state_path(farm_dir)
    try:
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except OSError as e:
        logger.error("state の保存に失敗しました: %s", e, exc_info=True)


def _zip_fingerprint(zip_path: Path) -> tuple[float, int] | None:
    """
    zip の fingerprint を (mtime, size) で返す。存在しなければ None。
    """
    try:
        stat = zip_path.stat()
        return (stat.st_mtime, stat.st_size)
    except OSError:
        return None


def _is_safe_extract_path(member_name: str, farm_dir: Path) -> bool:
    """
    Zip Slip 対策: 絶対パスや .. を含む危険なパスなら False。
    member_name が farm_dir 直下に正規化したときに farm_dir の外に出ないかも確認する。
    """
    # 空や絶対パス風は拒否
    if not member_name or member_name.strip() in ("", "."):
        return False
    # Windows のドライブ付き絶対パス
    if len(member_name) >= 2 and member_name[1] == ":":
        return False
    # 先頭の / で始まる絶対パス
    if member_name.startswith("/"):
        return False
    # .. を含むと親ディレクトリへ出る可能性
    if ".." in member_name:
        return False
    # 正規化して farm_dir の下にあるか確認
    try:
        resolved = (farm_dir / member_name).resolve()
        farm_resolved = farm_dir.resolve()
        return resolved == farm_resolved or str(resolved).startswith(str(farm_resolved) + os.sep)
    except (OSError, ValueError):
        return False


def _extract_one_zip(
    zip_path: Path,
    farm_dir: Path,
    state: dict[str, Any],
    logger: logging.Logger,
) -> None:
    """
    1つの zip を farm 直下へ展開する。
    各ファイルは一時ファイルに書き込んでから os.replace で置換（可能な範囲で atomic）。
    既存ファイルは上書きする。危険なパスはスキップする。
    """
    zip_name = zip_path.name
    updated_state: dict[str, Any] = {}
    try:
        # 日本語ファイル名対応: metadata_encoding を指定（Python 3.11+）
        with zipfile.ZipFile(zip_path, "r", metadata_encoding="utf-8") as zf:
            for info in zf.infolist():
                name = info.filename
                # ディレクトリはスキップ（ファイル展開時に親ディレクトリは自動作成される）
                if name.endswith("/"):
                    continue
                # 危険なパスは拒否
                if not _is_safe_extract_path(name, farm_dir):
                    logger.warning("Zip Slip 対策: 危険なパスをスキップしました zip=%s path=%s", zip_name, name)
                    continue
                target = farm_dir / name
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with zf.open(info) as src:
                        data = src.read()
                    # 一時ファイルに書いてから置換（atomic replace、同一ディレクトリで行う）
                    fd, tmp_path = tempfile.mkstemp(dir=target.parent, prefix=".zip_tmp_", suffix="")
                    try:
                        os.write(fd, data)
                        os.close(fd)
                        fd = -1
                        os.replace(tmp_path, target)
                        if target.exists() and target.stat().st_size != len(data):
                            logger.info("上書き: %s -> %s", zip_name, target)
                    finally:
                        if fd >= 0:
                            try:
                                os.close(fd)
                            except OSError:
                                pass
                        if Path(tmp_path).exists():
                            try:
                                os.remove(tmp_path)
                            except OSError:
                                pass
                except Exception as e:
                    logger.error("展開中エラー zip=%s member=%s: %s", zip_name, name, e, exc_info=True)
    except zipfile.BadZipFile as e:
        logger.error("ZIP が壊れています: %s - %s", zip_path, e, exc_info=True)
        raise
    except Exception as e:
        logger.error("ZIP 展開に失敗しました: %s - %s", zip_path, e, exc_info=True)
        raise


def extract_all_zips_on_start(
    zip_dir: str | Path = ZIP_DIR,
    farm_dir: str | Path = FARM_DIR,
) -> None:
    """
    zip_dir 内の *.zip を farm_dir 直下へ、前回より新しいものだけ展開する。
    状態は farm_dir 配下の .zip_extract_state.json に保持し、
    ログは farm_dir/zip_extract.log に UTF-8 で出力する。
    """
    zip_dir = Path(zip_dir)
    farm_dir = Path(farm_dir)
    _ensure_farm_dir(farm_dir)
    logger = _setup_logger(farm_dir)
    state = _load_state(farm_dir, logger)

    from datetime import datetime
    now_iso = datetime.now().isoformat()

    zips_found = sorted(zip_dir.glob("*.zip")) + sorted(zip_dir.glob("*.ZIP"))
    # 大文字小文字で重複する場合は1つに（Windowsでは同じファイル）
    seen_basename = set()
    unique_zips: list[Path] = []
    for p in zips_found:
        key = p.name.lower()
        if key not in seen_basename:
            seen_basename.add(key)
            unique_zips.append(p)

    for zip_path in unique_zips:
        zip_name = zip_path.name
        fp = _zip_fingerprint(zip_path)
        if fp is None:
            logger.warning("ファイルを開けませんでした（スキップ）: %s", zip_path)
            continue
        mtime, size = fp
        prev = (state.get("zips") or {}).get(zip_name)
        if prev is not None and prev.get("mtime") == mtime and prev.get("size") == size:
            logger.info("skip/no change: %s (mtime=%s, size=%s)", zip_name, mtime, size)
            continue
        try:
            _extract_one_zip(zip_path, farm_dir, {"mtime": mtime, "size": size}, logger)
            if "zips" not in state:
                state["zips"] = {}
            state["zips"][zip_name] = {
                "mtime": mtime,
                "size": size,
                "last_extracted_at": now_iso,
            }
            _save_state(farm_dir, state, logger)
            logger.info("extracted, updated state: %s", zip_name)
        except Exception:
            # ログは _extract_one_zip 内で既に出力済み。他 zip は続行
            logger.error("zip の処理をスキップして続行します: %s", zip_name)


# ---------------------------------------------------------------------------
# 動作確認用の簡易テスト（任意）
# ---------------------------------------------------------------------------
def _run_simple_test() -> None:
    """
    一時ディレクトリで ZIP 展開の流れを確認する。
    実行: python -m app.services.zip_extract_service
    """
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        zip_dir = Path(tmp) / "zip"
        farm_dir = Path(tmp) / "farm"
        zip_dir.mkdir()
        # テスト用の小さな zip を作成
        zip_path = zip_dir / "test.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("hello.txt", "Hello from zip_extract_service test.")
        # 1回目: 展開される
        extract_all_zips_on_start(zip_dir=zip_dir, farm_dir=farm_dir)
        assert (farm_dir / "hello.txt").exists()
        assert (farm_dir / "hello.txt").read_text(encoding="utf-8") == "Hello from zip_extract_service test."
        state_path = farm_dir / STATE_FILENAME
        assert state_path.exists()
        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)
        assert "test.zip" in state.get("zips", {})
        # 2回目: skip/no change
        extract_all_zips_on_start(zip_dir=zip_dir, farm_dir=farm_dir)
        log_path = farm_dir / LOG_FILENAME
        assert "skip/no change" in log_path.read_text(encoding="utf-8")
        # ログファイルを閉じる（Windows で temp 削除時に PermissionError が出ないよう）
        _logger = logging.getLogger("zip_extract")
        for h in _logger.handlers[:]:
            h.close()
            _logger.removeHandler(h)
    print("簡易テスト OK")


if __name__ == "__main__":
    _run_simple_test()
