"""
database.py - SQLite 누적 관리 모듈
history.db에 주별 데이터 UPSERT + 최대 52주 유지
"""

import logging
import sqlite3
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────
# DDL
# ─────────────────────────────────────────

DDL_WEEKLY_MARKET = """
CREATE TABLE IF NOT EXISTS weekly_market (
    week_date           TEXT,
    market              TEXT,
    index_close         REAL,
    weekly_pt_change    REAL,
    weekly_pct_change   REAL,
    daily_pt_change     REAL,
    daily_pct_change    REAL,
    weekly_foreign_net  REAL,
    weekly_inst_net     REAL,
    weekly_individual_net REAL,
    PRIMARY KEY (week_date, market)
);
"""

DDL_WEEKLY_CAP_WEIGHT = """
CREATE TABLE IF NOT EXISTS weekly_cap_weight (
    week_date   TEXT,
    market      TEXT,
    group_name  TEXT,
    stock_count INTEGER,
    group_cap   REAL,
    total_cap   REAL,
    weight_pct  REAL,
    PRIMARY KEY (week_date, market, group_name)
);
"""

DDL_WEEKLY_SECTOR = """
CREATE TABLE IF NOT EXISTS weekly_sector (
    week_date           TEXT,
    market              TEXT,
    sector              TEXT,
    stock_count         INTEGER,
    total_market_cap    REAL,
    foreign_net         REAL,
    inst_net            REAL,
    foreign_cap_ratio   REAL,
    inst_cap_ratio      REAL,
    avg_return_1w       REAL,
    PRIMARY KEY (week_date, market, sector)
);
"""

DDL_WEEKLY_STOCK = """
CREATE TABLE IF NOT EXISTS weekly_stock (
    week_date      TEXT,
    market         TEXT,
    ticker         TEXT,
    inst_net_1w    REAL,
    foreign_net_1w REAL,
    flow_source    TEXT,
    PRIMARY KEY (week_date, market, ticker)
);
"""


# ─────────────────────────────────────────
# DB 클래스
# ─────────────────────────────────────────

class Database:
    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self):
        with self._connect() as conn:
            conn.execute(DDL_WEEKLY_MARKET)
            conn.execute(DDL_WEEKLY_CAP_WEIGHT)
            conn.execute(DDL_WEEKLY_SECTOR)
            conn.execute(DDL_WEEKLY_STOCK)
            self._migrate_flow_source(conn)
        logger.info(f"DB 초기화 완료: {self.db_path}")

    def _migrate_flow_source(self, conn: sqlite3.Connection):
        """기존 DB에 flow_source 컬럼 추가 + 과거 행은 'naver' 소급 (스펙 §4.6)"""
        cols = [r[1] for r in conn.execute("PRAGMA table_info(weekly_stock)")]
        if "flow_source" not in cols:
            conn.execute("ALTER TABLE weekly_stock ADD COLUMN flow_source TEXT")
            logger.info("weekly_stock: flow_source 컬럼 추가")
        conn.execute("UPDATE weekly_stock SET flow_source='naver' WHERE flow_source IS NULL")

    # ─────────────────────────────────────
    # UPSERT
    # ─────────────────────────────────────

    def upsert_week(
        self,
        week_date: str,
        market_data: dict,
        cap_weight_data: pd.DataFrame,
        sector_data: pd.DataFrame,
        max_weeks: int = 52,
        processed: Optional[dict] = None,
    ):
        """주간 데이터 저장 (INSERT OR REPLACE) + 오래된 주 삭제"""
        with self._connect() as conn:
            # weekly_market
            self._upsert_market(conn, week_date, market_data)

            # weekly_cap_weight
            self._upsert_cap_weight(conn, week_date, cap_weight_data)

            # weekly_sector
            self._upsert_sector(conn, week_date, sector_data)

            # weekly_stock (per-stock 기관/외국인 1주 매매)
            if processed:
                self._upsert_stock(conn, week_date, processed)

            # 오래된 주 정리
            self._trim_old_weeks(conn, "weekly_market", max_weeks)
            self._trim_old_weeks(conn, "weekly_cap_weight", max_weeks)
            self._trim_old_weeks(conn, "weekly_sector", max_weeks)
            self._trim_old_weeks(conn, "weekly_stock", max_weeks)

        logger.info(f"DB UPSERT 완료: {week_date}")

    def _upsert_market(self, conn: sqlite3.Connection, week_date: str, market_data: dict):
        """
        market_data 예시:
        {
            "KOSPI": {"index_close": 2500.0, "weekly_pct_change": 1.2, ...},
            "KOSDAQ": {...}
        }
        """
        for market, info in market_data.items():
            conn.execute(
                """
                INSERT OR REPLACE INTO weekly_market
                (week_date, market, index_close, weekly_pt_change, weekly_pct_change,
                 daily_pt_change, daily_pct_change,
                 weekly_foreign_net, weekly_inst_net, weekly_individual_net)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    week_date,
                    market,
                    info.get("index_close"),
                    info.get("weekly_pt_change"),
                    info.get("weekly_pct_change"),
                    info.get("daily_pt_change"),
                    info.get("daily_pct_change"),
                    info.get("weekly_foreign_net"),
                    info.get("weekly_inst_net"),
                    info.get("weekly_individual_net"),
                ),
            )

    def _upsert_cap_weight(self, conn: sqlite3.Connection, week_date: str, df: pd.DataFrame):
        if df is None or df.empty:
            return
        # df 컬럼: [시장, 구간, 종목수, 구간시총(억), 전체시총(억), 비중(%)]
        for _, row in df.iterrows():
            conn.execute(
                """
                INSERT OR REPLACE INTO weekly_cap_weight
                (week_date, market, group_name, stock_count, group_cap, total_cap, weight_pct)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    week_date,
                    row.get("시장", ""),
                    row.get("구간", ""),
                    int(row.get("종목수", 0)),
                    row.get("구간시총(억)", None),
                    row.get("전체시총(억)", None),
                    row.get("비중(%)", None),
                ),
            )

    def _upsert_sector(self, conn: sqlite3.Connection, week_date: str, df: pd.DataFrame):
        if df is None or df.empty:
            return
        for _, row in df.iterrows():
            conn.execute(
                """
                INSERT OR REPLACE INTO weekly_sector
                (week_date, market, sector, stock_count, total_market_cap,
                 foreign_net, inst_net, foreign_cap_ratio, inst_cap_ratio, avg_return_1w)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    week_date,
                    row.get("시장", ""),
                    row.get("섹터", ""),
                    int(row.get("종목수", 0)),
                    row.get("시가총액합(억)", None),
                    row.get("1주외국인매매합(억)", None),
                    row.get("1주기관매매합(억)", None),
                    row.get("외국인_시총대비비중(%)", None),
                    row.get("기관_시총대비비중(%)", None),
                    row.get("1주등락률평균(%)", None),
                ),
            )

    def _upsert_stock(self, conn: sqlite3.Connection, week_date: str, processed: dict):
        """per-stock 기관/외국인 1주 매매 데이터 저장 (데이터 있는 종목만)"""
        flow_source = processed.get("flow_source", "naver")
        for market in ["KOSPI", "KOSDAQ"]:
            df = processed.get(market.lower(), pd.DataFrame())
            if df.empty:
                continue
            for _, row in df.iterrows():
                ticker = row.get("티커")
                inst = row.get("1주기관매매")
                fore = row.get("1주외국인매매")
                if not ticker:
                    continue
                inst_val = float(inst) if inst is not None and pd.notna(inst) else None
                fore_val = float(fore) if fore is not None and pd.notna(fore) else None
                if inst_val is None and fore_val is None:
                    continue
                conn.execute(
                    """
                    INSERT OR REPLACE INTO weekly_stock
                    (week_date, market, ticker, inst_net_1w, foreign_net_1w, flow_source)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (week_date, market, ticker, inst_val, fore_val, flow_source),
                )

    def _trim_old_weeks(self, conn: sqlite3.Connection, table: str, max_weeks: int):
        """가장 오래된 주를 삭제하여 max_weeks 유지"""
        rows = conn.execute(
            f"SELECT DISTINCT week_date FROM {table} ORDER BY week_date ASC"
        ).fetchall()
        if len(rows) > max_weeks:
            to_delete = rows[:len(rows) - max_weeks]
            for (wd,) in to_delete:
                conn.execute(f"DELETE FROM {table} WHERE week_date = ?", (wd,))
            logger.info(f"{table}: {len(to_delete)}개 오래된 주 삭제")

    # ─────────────────────────────────────
    # 조회
    # ─────────────────────────────────────

    def get_market_history(self, market: str, n_weeks: int = 52) -> pd.DataFrame:
        """최근 n_weeks 주 지수/매매동향 이력"""
        with self._connect() as conn:
            df = pd.read_sql_query(
                """
                SELECT * FROM weekly_market
                WHERE market = ?
                ORDER BY week_date DESC
                LIMIT ?
                """,
                conn,
                params=(market, n_weeks),
            )
        return df.sort_values("week_date").reset_index(drop=True)

    def get_cap_weight_history(self, market: str, n_weeks: int = 52) -> pd.DataFrame:
        """최근 n_weeks 주 시가 구간 비중 이력"""
        with self._connect() as conn:
            df = pd.read_sql_query(
                """
                SELECT * FROM weekly_cap_weight
                WHERE market = ?
                ORDER BY week_date DESC
                LIMIT ?
                """,
                conn,
                params=(market, n_weeks * 20),  # 구간이 여러 개이므로 여유분
            )
        return df.sort_values("week_date").reset_index(drop=True)

    def get_sector_history(self, market: str, n_weeks: int = 8) -> pd.DataFrame:
        """최근 n_weeks 주 섹터별 매매동향 이력"""
        # 최근 n_weeks 개의 week_date 조회
        with self._connect() as conn:
            weeks = conn.execute(
                """
                SELECT DISTINCT week_date FROM weekly_sector
                WHERE market = ?
                ORDER BY week_date DESC
                LIMIT ?
                """,
                (market, n_weeks),
            ).fetchall()
            if not weeks:
                return pd.DataFrame()
            week_dates = [w[0] for w in weeks]
            placeholders = ",".join("?" * len(week_dates))
            df = pd.read_sql_query(
                f"""
                SELECT * FROM weekly_sector
                WHERE market = ? AND week_date IN ({placeholders})
                ORDER BY week_date ASC, total_market_cap DESC
                """,
                conn,
                params=[market] + week_dates,
            )
        return df.reset_index(drop=True)

    def get_latest_week_date(self) -> Optional[str]:
        """가장 최근 week_date 반환"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT MAX(week_date) FROM weekly_market"
            ).fetchone()
        return row[0] if row else None

    def get_week_count(self) -> int:
        """DB에 저장된 총 주(week) 수 (weekly_market 기준 — 지수/등락 계산용)"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(DISTINCT week_date) FROM weekly_market"
            ).fetchone()
        return row[0] if row else 0

    def get_stock_week_count(self) -> int:
        """weekly_stock에 저장된 총 주(week) 수 (다기간 기관/외국인 매매 계산용)"""
        with self._connect() as conn:
            try:
                row = conn.execute(
                    "SELECT COUNT(DISTINCT week_date) FROM weekly_stock"
                ).fetchone()
                return row[0] if row else 0
            except Exception:
                return 0

    def get_prev_index_close(self, market: str, current_week: str) -> Optional[float]:
        """current_week 직전 주의 index_close 반환"""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT index_close FROM weekly_market
                WHERE market = ? AND week_date < ?
                ORDER BY week_date DESC LIMIT 1
                """,
                (market, current_week),
            ).fetchone()
        return row[0] if row else None

    def get_market_flow_from_stock(self, market: str, n_weeks: int = 52) -> pd.DataFrame:
        """weekly_stock 테이블의 종목별 매매를 주(week)별로 합산하여
        시장 전체 외국인/기관 순매수 추이를 반환.
        weekly_market 테이블의 weekly_foreign_net/inst_net이 NULL일 때 대체 데이터.
        Returns: DataFrame [week_date, weekly_foreign_net, weekly_inst_net]
        """
        with self._connect() as conn:
            df = pd.read_sql_query(
                """
                SELECT week_date,
                       SUM(foreign_net_1w) AS weekly_foreign_net,
                       SUM(inst_net_1w)    AS weekly_inst_net
                FROM weekly_stock
                WHERE market = ?
                GROUP BY week_date
                ORDER BY week_date DESC
                LIMIT ?
                """,
                conn,
                params=(market, n_weeks),
            )
        return df.sort_values("week_date").reset_index(drop=True)

    def get_stock_investor_accumulate(self, market: str, n_weeks: int) -> pd.DataFrame:
        """최근 n_weeks 주 기관/외국인 매매 종목별 누적 합산
        Returns: DataFrame with columns [ticker, inst_net_sum, foreign_net_sum]
        """
        with self._connect() as conn:
            weeks = conn.execute(
                """
                SELECT DISTINCT week_date FROM weekly_stock
                WHERE market = ? ORDER BY week_date DESC LIMIT ?
                """,
                (market, n_weeks),
            ).fetchall()
            if not weeks:
                return pd.DataFrame()
            week_dates = [w[0] for w in weeks]
            placeholders = ",".join("?" * len(week_dates))
            df = pd.read_sql_query(
                f"""
                SELECT ticker,
                       SUM(inst_net_1w)    AS inst_net_sum,
                       SUM(foreign_net_1w) AS foreign_net_sum
                FROM weekly_stock
                WHERE market = ? AND week_date IN ({placeholders})
                GROUP BY ticker
                """,
                conn,
                params=[market] + week_dates,
            )
        return df

    def get_stock_flow_history(self, ticker: str, market: str, n_weeks: int = 4) -> pd.DataFrame:
        """종목 하나의 최근 n_weeks 주간 기관/외국인 순매수 이력 (오름차순).
        Returns: DataFrame [week_date, inst_net_1w, foreign_net_1w]
        """
        with self._connect() as conn:
            df = pd.read_sql_query(
                """
                SELECT week_date, inst_net_1w, foreign_net_1w
                FROM weekly_stock
                WHERE ticker = ? AND market = ?
                ORDER BY week_date DESC LIMIT ?
                """,
                conn, params=(ticker, market, n_weeks),
            )
        return df.sort_values("week_date").reset_index(drop=True)


# ─────────────────────────────────────────
# 모듈 레벨 함수 (main.py 인터페이스)
# ─────────────────────────────────────────

def upsert_week(
    db: Database,
    week_date: str,
    processed: dict,
    max_weeks: int = 52,
):
    """처리된 데이터를 DB에 저장"""
    market_data = _build_market_data(processed)

    # cap_weight: KOSPI + KOSDAQ 합쳐서 시장 컬럼 포함
    cap_dfs = []
    for market in ["KOSPI", "KOSDAQ"]:
        key = f"{market.lower()}_cap_weight"
        df = processed.get(key, pd.DataFrame())
        if not df.empty:
            df = df.copy()
            df["시장"] = market
            cap_dfs.append(df)
    cap_weight_df = pd.concat(cap_dfs, ignore_index=True) if cap_dfs else pd.DataFrame()

    # sector: KOSPI + KOSDAQ 합쳐서 시장 컬럼 포함
    sec_dfs = []
    for market in ["KOSPI", "KOSDAQ"]:
        key = f"{market.lower()}_sector"
        df = processed.get(key, pd.DataFrame())
        if not df.empty:
            df = df.copy()
            df["시장"] = market
            sec_dfs.append(df)
    sector_df = pd.concat(sec_dfs, ignore_index=True) if sec_dfs else pd.DataFrame()

    db.upsert_week(
        week_date=week_date,
        market_data=market_data,
        cap_weight_data=cap_weight_df,
        sector_data=sector_df,
        max_weeks=max_weeks,
        processed=processed,  # per-stock 기관/외국인 1주 매매 저장용
    )


def _build_market_data(processed: dict) -> dict:
    """processed에서 weekly_market 테이블용 dict 생성 (KIS API 기반)"""
    market_info = processed.get("market_info", {})
    investor_ranks = processed.get("investor_ranks", {})
    result = {}

    for market in ["KOSPI", "KOSDAQ"]:
        info = {}
        # KIS API 지수 데이터
        idx = market_info.get(f"{market}_index", {})
        if idx:
            info["index_close"] = idx.get("종가")
            info["daily_pt_change"] = idx.get("전일대비")
            info["daily_pct_change"] = idx.get("등락률")

        # 기관/외국인 순매수 합산 (investor_ranks에서 집계)
        inv_df = investor_ranks.get(market, pd.DataFrame())
        if not inv_df.empty:
            if "기관순매수금액" in inv_df.columns:
                info["weekly_inst_net"] = float(inv_df["기관순매수금액"].sum())
            if "외국인순매수금액" in inv_df.columns:
                info["weekly_foreign_net"] = float(inv_df["외국인순매수금액"].sum())

        result[market] = info

    return result
