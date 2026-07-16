# tests/test_krx_flows.py
from unittest.mock import patch

import pandas as pd
import pytest

from modules import crawler


def _krx_df(tickers, amounts_won):
    """pykrx 반환 형태 재현: index=티커, 순매수거래대금(원)"""
    return pd.DataFrame(
        {"종목명": [f"종목{t}" for t in tickers],
         "순매수거래대금": amounts_won},
        index=pd.Index(tickers, name="티커"),
    )


def test_crawl_krx_investor_flows_merges_markets_and_investors():
    def fake(fromdate, todate, market, investor):
        assert (fromdate, todate) == ("20260713", "20260717")
        base = {"KOSPI": ["005930"], "KOSDAQ": ["380540"]}[market]
        amt = {"기관합계": [1.5e8], "외국인": [-2.0e8]}[investor]
        return _krx_df(base, amt)

    with patch("pykrx.stock.get_market_net_purchases_of_equities_by_ticker",
               side_effect=fake):
        df = crawler.crawl_krx_investor_flows("20260713", "20260717")

    assert sorted(df.columns) == sorted(["티커", "시장", "1주기관매매", "1주외국인매매"])
    row = df[df["티커"] == "005930"].iloc[0]
    assert row["시장"] == "KOSPI"
    assert row["1주기관매매"] == pytest.approx(1.5)   # 원 → 억원
    assert row["1주외국인매매"] == pytest.approx(-2.0)
    assert set(df["티커"]) == {"005930", "380540"}


def test_crawl_krx_investor_flows_raises_on_empty():
    with patch("pykrx.stock.get_market_net_purchases_of_equities_by_ticker",
               return_value=pd.DataFrame()):
        with pytest.raises(RuntimeError):
            crawler.crawl_krx_investor_flows("20260713", "20260717")


def test_collect_all_falls_back_on_krx_failure():
    """KRX 실패 시 flow_source=naver 폴백 분기 (collect_all과 동일 로직)"""
    result = {}
    try:
        with patch("pykrx.stock.get_market_net_purchases_of_equities_by_ticker",
                   side_effect=Exception("LOGOUT")):
            result["krx_flows"] = crawler.crawl_krx_investor_flows("20260713", "20260717")
            result["flow_source"] = "krx"
    except Exception:
        result["krx_flows"] = pd.DataFrame()
        result["flow_source"] = "naver"
    assert result["flow_source"] == "naver"
    assert result["krx_flows"].empty
