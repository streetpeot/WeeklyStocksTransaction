import sqlite3

import pandas as pd
import pytest

from modules import database


@pytest.fixture
def db(tmp_path):
    return database.Database(str(tmp_path / "test.db"))


def _processed(flow_source="krx"):
    return {
        "kospi": pd.DataFrame({
            "티커": ["005930"], "1주기관매매": [1.5], "1주외국인매매": [-2.0],
        }),
        "kosdaq": pd.DataFrame(),
        "flow_source": flow_source,
    }


def test_flow_source_column_exists_and_saved(db):
    with db._connect() as conn:
        db._upsert_stock(conn, "20260717", _processed("krx"))
        row = conn.execute(
            "SELECT flow_source FROM weekly_stock WHERE ticker='005930'"
        ).fetchone()
    assert row[0] == "krx"


def test_migration_backfills_naver(tmp_path):
    """flow_source 없는 기존 DB → 마이그레이션 후 기존 행 'naver' 소급"""
    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE weekly_stock (
        week_date TEXT, market TEXT, ticker TEXT,
        inst_net_1w REAL, foreign_net_1w REAL,
        PRIMARY KEY (week_date, market, ticker))""")
    conn.execute("INSERT INTO weekly_stock VALUES ('20260710','KOSPI','005930',10.0,20.0)")
    conn.commit()
    conn.close()

    db = database.Database(path)  # _init_db가 마이그레이션 수행
    with db._connect() as conn:
        row = conn.execute(
            "SELECT flow_source FROM weekly_stock WHERE week_date='20260710'"
        ).fetchone()
    assert row[0] == "naver"


def test_get_stock_flow_history(db):
    with db._connect() as conn:
        for wd, inst in [("20260703", 1.0), ("20260710", 2.0), ("20260717", 3.0)]:
            conn.execute(
                "INSERT INTO weekly_stock (week_date, market, ticker, inst_net_1w, foreign_net_1w, flow_source)"
                " VALUES (?, 'KOSPI', '005930', ?, ?, 'krx')", (wd, inst, -inst))
    hist = db.get_stock_flow_history("005930", "KOSPI", n_weeks=2)
    assert list(hist["week_date"]) == ["20260710", "20260717"]  # 오름차순, 최근 2주
    assert list(hist["inst_net_1w"]) == [2.0, 3.0]


def test_upsert_stock_persists_etf(db):
    processed = {
        "kospi": pd.DataFrame(), "kosdaq": pd.DataFrame(),
        "flow_source": "krx",
        "etf_flows": pd.DataFrame({
            "티커": ["069500"], "종목명": ["KODEX 200"],
            "1주기관매매": [-100.0], "1주외국인매매": [50.0],
        }),
    }
    with db._connect() as conn:
        db._upsert_stock(conn, "20260717", processed)
        row = conn.execute(
            "SELECT market, inst_net_1w, foreign_net_1w, flow_source "
            "FROM weekly_stock WHERE ticker='069500'"
        ).fetchone()
    assert row == ("ETF", -100.0, 50.0, "krx")


def test_etf_rows_inert_to_market_queries(db):
    """ETF 행은 KOSPI/KOSDAQ 조회에 영향 없음."""
    processed = {
        "kospi": pd.DataFrame({"티커": ["005930"], "1주기관매매": [1.0], "1주외국인매매": [2.0]}),
        "kosdaq": pd.DataFrame(),
        "flow_source": "krx",
        "etf_flows": pd.DataFrame({"티커": ["069500"], "종목명": ["KODEX 200"],
                                   "1주기관매매": [-100.0], "1주외국인매매": [50.0]}),
    }
    with db._connect() as conn:
        db._upsert_stock(conn, "20260717", processed)
    acc = db.get_stock_investor_accumulate("KOSPI", 1)
    assert list(acc["ticker"]) == ["005930"]     # ETF 미포함
