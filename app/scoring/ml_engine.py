"""LightGBM-based sow excellence classifier with SHAP explanations.

MLEngine wraps model training (GroupKFold CV), prediction, SHAP value
computation, and model persistence.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import lightgbm as lgb
import shap
from sklearn.model_selection import GroupKFold
from sklearn.metrics import roc_auc_score, accuracy_score, f1_score

from app.scoring.ml_features import (
    FEATURE_COLS,
    build_feature_matrix,
)

MODEL_DIR = Path(__file__).resolve().parent.parent.parent / "models"
MODEL_PATH = MODEL_DIR / "lgbm_sow.txt"

# Conservative hyperparameters for small dataset (~7,800 rows)
LGB_PARAMS = {
    "objective": "binary",
    "metric": "auc",
    "num_leaves": 15,
    "max_depth": 5,
    "min_child_samples": 20,
    "learning_rate": 0.05,
    "n_estimators": 300,
    "lambda_l1": 0.1,
    "lambda_l2": 1.0,
    "subsample": 0.8,
    "subsample_freq": 1,
    "verbose": -1,
    "random_state": 42,
}


class MLEngine:
    """LightGBM sow excellence classifier."""

    def __init__(self):
        self.model: lgb.LGBMClassifier | None = None
        self.version: str = ""
        self._feature_matrix: pd.DataFrame | None = None

    def train(self, conn: sqlite3.Connection,
              progress_cb=None) -> dict:
        """Train with 5-fold GroupKFold CV grouped by individual_id.

        Returns dict with cv_auc, cv_accuracy, cv_f1, label_balance.
        """
        def _p(msg: str):
            if progress_cb:
                progress_cb(msg)

        _p("特徴量構築中...")
        df = build_feature_matrix(conn)
        self._feature_matrix = df

        X = df[FEATURE_COLS].copy()
        y = df["is_excellent"].values
        groups = df["individual_id"].values

        # Report label balance
        n_pos = int(y.sum())
        n_total = len(y)
        _p(f"ラベル分布: 優秀={n_pos} / 全体={n_total} "
           f"({n_pos / n_total * 100:.1f}%)")

        # 5-fold GroupKFold
        gkf = GroupKFold(n_splits=5)
        oof_probs = np.zeros(len(y))
        oof_preds = np.zeros(len(y), dtype=int)

        for fold, (train_idx, val_idx) in enumerate(
                gkf.split(X, y, groups), 1):
            _p(f"Fold {fold}/5 学習中...")
            X_train, X_val = X.iloc[train_idx], X.iloc[val_idx]
            y_train, y_val = y[train_idx], y[val_idx]

            model = lgb.LGBMClassifier(**LGB_PARAMS)
            model.fit(
                X_train, y_train,
                eval_set=[(X_val, y_val)],
                callbacks=[lgb.log_evaluation(period=0)],
            )

            probs = model.predict_proba(X_val)[:, 1]
            oof_probs[val_idx] = probs
            oof_preds[val_idx] = (probs >= 0.5).astype(int)

        cv_auc = roc_auc_score(y, oof_probs)
        cv_acc = accuracy_score(y, oof_preds)
        cv_f1 = f1_score(y, oof_preds, zero_division=0)

        _p(f"CV結果: AUC={cv_auc:.4f}  Acc={cv_acc:.4f}  F1={cv_f1:.4f}")

        # Train final model on all data
        _p("最終モデル学習中...")
        self.model = lgb.LGBMClassifier(**LGB_PARAMS)
        self.model.fit(X, y)
        self.version = datetime.now().strftime("%Y%m%d_%H%M%S")

        self.save_model()
        _p("モデル保存完了")

        return {
            "cv_auc": cv_auc,
            "cv_accuracy": cv_acc,
            "cv_f1": cv_f1,
            "n_positive": n_pos,
            "n_total": n_total,
        }

    def predict_all(self, conn: sqlite3.Connection,
                    progress_cb=None) -> pd.DataFrame:
        """Predict all records and save to ml_predictions table.

        Returns DataFrame with individual_id, parity, prob, shap values.
        """
        def _p(msg: str):
            if progress_cb:
                progress_cb(msg)

        if self.model is None:
            raise RuntimeError("モデルが未学習です。先にtrain()を実行してください。")

        _p("全レコード予測中...")
        if self._feature_matrix is None:
            self._feature_matrix = build_feature_matrix(conn)

        df = self._feature_matrix
        X = df[FEATURE_COLS]
        probs = self.model.predict_proba(X)[:, 1]

        _p("SHAP値計算中...")
        explainer = shap.TreeExplainer(self.model)
        shap_values = explainer.shap_values(X)
        # For binary classification, shap_values may be a list [neg, pos]
        if isinstance(shap_values, list):
            shap_values = shap_values[1]

        # Save to DB
        _p("予測結果保存中...")
        now = datetime.now().isoformat()
        conn.execute("DELETE FROM ml_predictions")

        results = []
        for i in range(len(df)):
            row = df.iloc[i]
            shap_dict = {
                FEATURE_COLS[j]: float(shap_values[i, j])
                for j in range(len(FEATURE_COLS))
            }
            conn.execute(
                """INSERT INTO ml_predictions
                   (individual_id, parity, pred_excellent_prob,
                    shap_json, model_version, predicted_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (row["individual_id"], int(row["parity"]),
                 float(probs[i]), json.dumps(shap_dict),
                 self.version, now),
            )
            results.append({
                "individual_id": row["individual_id"],
                "parity": int(row["parity"]),
                "prob": float(probs[i]),
            })

        conn.commit()
        _p(f"予測完了: {len(results)}件")
        return pd.DataFrame(results)

    def get_global_shap(self, conn: sqlite3.Connection
                        ) -> tuple[list[str], np.ndarray]:
        """Compute mean |SHAP| for global feature importance.

        Returns (feature_names, mean_abs_shap_values).
        """
        if self.model is None:
            raise RuntimeError("モデルが未学習です。")

        if self._feature_matrix is None:
            self._feature_matrix = build_feature_matrix(conn)

        X = self._feature_matrix[FEATURE_COLS]
        explainer = shap.TreeExplainer(self.model)
        shap_values = explainer.shap_values(X)
        if isinstance(shap_values, list):
            shap_values = shap_values[1]

        mean_abs = np.abs(shap_values).mean(axis=0)
        return FEATURE_COLS, mean_abs

    def get_individual_shap(self, conn: sqlite3.Connection,
                            individual_id: str,
                            parity: int | None = None
                            ) -> shap.Explanation | None:
        """Get SHAP Explanation for a specific sow (and optionally parity)."""
        if self.model is None:
            raise RuntimeError("モデルが未学習です。")

        if self._feature_matrix is None:
            self._feature_matrix = build_feature_matrix(conn)

        mask = self._feature_matrix["individual_id"] == individual_id
        if parity is not None:
            mask &= self._feature_matrix["parity"] == parity

        subset = self._feature_matrix[mask]
        if subset.empty:
            return None

        X = subset[FEATURE_COLS]
        explainer = shap.TreeExplainer(self.model)
        shap_values = explainer(X)
        return shap_values

    def save_model(self) -> None:
        """Save trained model to disk."""
        if self.model is None:
            return
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        self.model.booster_.save_model(str(MODEL_PATH))

    def load_model(self) -> bool:
        """Load model from disk. Returns True if successful."""
        if not MODEL_PATH.exists():
            return False
        booster = lgb.Booster(model_file=str(MODEL_PATH))
        self.model = lgb.LGBMClassifier(**LGB_PARAMS)
        self.model._Booster = booster
        self.model.fitted_ = True
        self.model._n_features = len(FEATURE_COLS)
        self.model._n_classes = 2
        self.model.classes_ = np.array([0, 1])
        self.version = "loaded"
        return True
