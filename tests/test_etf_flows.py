from unittest.mock import patch

import pandas as pd
import pytest

from modules import crawler


def _investor_df(net_won: dict) -> pd.DataFrame:
    """get_etf_trading_volume_and_value 반환 형태 재현: index=투자자, columns MultiIndex, ('거래대금','순매수')만 채움."""
    idx = pd.Index(list(net_won.keys()), name="INVST_NM")
    df = pd.DataFrame({("거래대금", "순매수"): list(net_won.values())}, index=idx)
    df.columns = pd.MultiIndex.from_tuples(df.columns)
    return df


def test_crawl_etf_flows_market_agg_and_per_ticker():
    agg_df = _investor_df({"기관합계": -19_568e8, "외국인": 5e8, "개인": 19_048e8})
    per = {
        "069500": _investor_df({"기관합계": -100e8, "외국인": 50e8}),
        "229200": _investor_df({"기관합계": -1_171e8, "외국인": 543e8}),
    }

    def fake(fromdate, todate, *args):
        # (from,to) → 시장 집계 / (from,to,ticker) → 개별
        if not args:
            return agg_df
        return per[args[0]]

    with patch("pykrx.stock.get_etf_trading_volume_and_value", side_effect=fake), \
         patch("pykrx.stock.get_etf_ticker_list", return_value=["069500", "229200"]), \
         patch("pykrx.stock.get_etf_ticker_name", side_effect=lambda t: {"069500": "KODEX 200", "229200": "KODEX 코스닥150"}[t]):
        flows, agg = crawler.crawl_krx_etf_flows("20260706", "20260710")

    assert agg == {"외국인": pytest.approx(5.0), "기관": pytest.approx(-19568.0), "개인": pytest.approx(19048.0)}
    assert sorted(flows.columns) == sorted(["티커", "종목명", "1주기관매매", "1주외국인매매"])
    row = flows[flows["티커"] == "229200"].iloc[0]
    assert row["종목명"] == "KODEX 코스닥150"
    assert row["1주외국인매매"] == pytest.approx(543.0)
    assert row["1주기관매매"] == pytest.approx(-1171.0)


def test_crawl_etf_flows_skips_failed_ticker():
    def fake(fromdate, todate, *args):
        if not args:
            raise Exception("agg fail")           # 집계 실패 → None
        if args[0] == "BAD":
            raise Exception("ticker fail")          # 개별 실패 → 스킵
        return _investor_df({"기관합계": 1e8, "외국인": 2e8})

    with patch("pykrx.stock.get_etf_trading_volume_and_value", side_effect=fake), \
         patch("pykrx.stock.get_etf_ticker_list", return_value=["BAD", "069500"]), \
         patch("pykrx.stock.get_etf_ticker_name", side_effect=lambda t: "이름"):
        flows, agg = crawler.crawl_krx_etf_flows("20260706", "20260710")

    assert agg is None
    assert list(flows["티커"]) == ["069500"]      # BAD 스킵


def test_collect_all_wires_etf(monkeypatch):
    def fake_market(market_code, max_pages=None):
        return pd.DataFrame({"티커": ["005930"], "종목명": ["삼성전자"], "시가총액(억)": [100.0]})

    etf_flows = pd.DataFrame({"티커": ["069500"], "종목명": ["KODEX 200"],
                              "1주기관매매": [-100.0], "1주외국인매매": [50.0]})
    with patch("modules.crawler.crawl_naver_market", side_effect=fake_market), \
         patch("modules.crawler.build_sector_map", return_value={}), \
         patch("modules.crawler.collect_kis_investor_rank", return_value={}), \
         patch("modules.crawler.collect_kis_market_info", return_value={}), \
         patch("modules.crawler.crawl_period_returns_all", return_value=pd.DataFrame()), \
         patch("modules.crawler.crawl_naver_stock_details", return_value=pd.DataFrame()), \
         patch("modules.crawler.crawl_krx_investor_flows", return_value=pd.DataFrame()), \
         patch("modules.crawler.crawl_krx_etf_flows", return_value=(etf_flows, {"외국인": 5.0})):
        result = crawler.collect_all({"kis": {"app_key": "", "app_secret": ""}})

    assert list(result["etf_flows"]["티커"]) == ["069500"]
    assert result["etf_market_agg"] == {"외국인": 5.0}
