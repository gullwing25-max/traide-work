"""J-Quants API V2 (Freeプラン) の日足取得クライアント。

- ベースURL: https://api.jquants.com/v2
- 認証: ヘッダ `x-api-key: <JQUANTS_API_KEY>` のみ(V1のトークン方式は使わない)
- date指定 (`GET /v2/equities/bars/daily?date=YYYYMMDD`) で、その日の全銘柄の四本値を取得
- cursorによるページングに対応
- 429は指数バックオフでリトライ
- レスポンスのキー名は版差がありうるため、「リストを持つキー」を拾う実装にする

APIキー・口座情報はログに出さない。
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

import pandas as pd
import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://api.jquants.com/v2"
DAILY_BARS_PATH = "/equities/bars/daily"

# 生値/調整済み値のどちらを使うか。列名は版差がありうるので候補を並べて先勝ちで探す。
ADJUSTED_COLUMN_CANDIDATES = {
    "open": ["AdjustmentOpen", "adjustment_open", "AdjOpen"],
    "high": ["AdjustmentHigh", "adjustment_high", "AdjHigh"],
    "low": ["AdjustmentLow", "adjustment_low", "AdjLow"],
    "close": ["AdjustmentClose", "adjustment_close", "AdjClose"],
}
RAW_COLUMN_CANDIDATES = {
    "open": ["Open", "open"],
    "high": ["High", "high"],
    "low": ["Low", "low"],
    "close": ["Close", "close"],
}
CODE_COLUMN_CANDIDATES = ["Code", "code", "LocalCode"]
DATE_COLUMN_CANDIDATES = ["Date", "date"]


class JQuantsClient:
    def __init__(
        self,
        api_key: str | None = None,
        cache_dir: str | Path = "data/bars",
        rate_limit_seconds: float = 12.0,
        max_retries: int = 6,
        session: requests.Session | None = None,
    ):
        self.api_key = api_key or os.environ.get("JQUANTS_API_KEY")
        if not self.api_key:
            raise ValueError(
                "JQUANTS_API_KEY が設定されていません。環境変数で渡してください。"
            )
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.rate_limit_seconds = rate_limit_seconds
        self.max_retries = max_retries
        self.session = session or requests.Session()
        self._last_request_ts: float | None = None
        self._logged_sample = False
        self._logged_adjusted_choice = False

    # -- 低レベル HTTP --------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {"x-api-key": self.api_key}

    def _throttle(self) -> None:
        if self._last_request_ts is None:
            return
        elapsed = time.monotonic() - self._last_request_ts
        wait = self.rate_limit_seconds - elapsed
        if wait > 0:
            time.sleep(wait)

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{BASE_URL}{path}"
        backoff = 2.0
        for attempt in range(1, self.max_retries + 1):
            self._throttle()
            self._last_request_ts = time.monotonic()
            resp = self.session.get(url, headers=self._headers(), params=params, timeout=30)
            if resp.status_code == 429:
                logger.warning(
                    "429 Too Many Requests (attempt %d/%d), %.1fs待機してリトライ",
                    attempt,
                    self.max_retries,
                    backoff,
                )
                time.sleep(backoff)
                backoff *= 2
                continue
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError(f"429が続いたためリトライを断念: {url} params={params}")

    @staticmethod
    def _find_list_key(payload: dict[str, Any]) -> str | None:
        for key, value in payload.items():
            if isinstance(value, list):
                return key
        return None

    def _fetch_all_pages(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        page_params = dict(params)
        first_logged = self._logged_sample
        while True:
            payload = self._get(path, page_params)
            if not first_logged:
                logger.info("J-Quants レスポンス構造(1件目、生JSON): %s", payload)
                first_logged = True
                self._logged_sample = True
            list_key = self._find_list_key(payload)
            if list_key is None:
                logger.warning("リストを持つキーが見つかりません: keys=%s", list(payload.keys()))
                break
            records.extend(payload[list_key])
            cursor = payload.get("pagination_key") or payload.get("cursor")
            if not cursor:
                break
            page_params = dict(params)
            page_params["pagination_key"] = cursor
        return records

    # -- 高レベル API -----------------------------------------------------

    def fetch_daily_bars(self, date: str) -> pd.DataFrame:
        """指定日(YYYYMMDD)の全銘柄日足を取得してDataFrameで返す。0件ならその日は非営業日/未対応。"""
        records = self._fetch_all_pages(DAILY_BARS_PATH, {"date": date})
        if not records:
            return pd.DataFrame(columns=["code", "date", "open", "high", "low", "close"])
        return self._normalize_records(records)

    def _normalize_records(self, records: list[dict[str, Any]]) -> pd.DataFrame:
        df = pd.DataFrame.from_records(records)

        code_col = next((c for c in CODE_COLUMN_CANDIDATES if c in df.columns), None)
        date_col = next((c for c in DATE_COLUMN_CANDIDATES if c in df.columns), None)
        if code_col is None or date_col is None:
            raise ValueError(f"code/date列が見つかりません。列: {list(df.columns)}")

        use_adjusted = all(
            any(c in df.columns for c in cands) for cands in ADJUSTED_COLUMN_CANDIDATES.values()
        )
        col_map_src = ADJUSTED_COLUMN_CANDIDATES if use_adjusted else RAW_COLUMN_CANDIDATES

        out = pd.DataFrame()
        out["code"] = df[code_col].astype(str)
        out["date"] = df[date_col].astype(str)
        for field, cands in col_map_src.items():
            src = next((c for c in cands if c in df.columns), None)
            if src is None:
                out[field] = pd.NA
            else:
                out[field] = pd.to_numeric(df[src], errors="coerce")

        if not self._logged_adjusted_choice:
            logger.info(
                "四本値は %s 列を使用します (use_adjusted=%s)",
                {k: v[0] for k, v in col_map_src.items()},
                use_adjusted,
            )
            self._logged_adjusted_choice = True

        return out

    # -- キャッシュ付き取得 -------------------------------------------------

    def cache_path(self, date: str) -> Path:
        return self.cache_dir / f"{date}.parquet"

    def get_daily_bars_cached(self, date: str) -> pd.DataFrame:
        """キャッシュがあれば読む。無ければ取得して保存(0件=非営業日/データ範囲外も含めてキャッシュし、再実行時の再取得を避ける)。"""
        path = self.cache_path(date)
        if path.exists():
            return pd.read_parquet(path)
        df = self.fetch_daily_bars(date)
        df.to_parquet(path, index=False)
        if df.empty:
            logger.info("%s: データなし(非営業日 or データ範囲外)", date)
        else:
            logger.info("%s: %d銘柄をキャッシュ (%s)", date, len(df), path)
        return df
