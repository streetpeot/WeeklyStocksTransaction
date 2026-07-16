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


def test_crawl_krx_investor_flows_outer_merge_preserves_mismatched_tickers():
    """같은 시장(KOSPI)에서 기관합계에만 있는 티커(AAA)·외국인에만 있는 티커(BBB)가
    outer merge로 모두 생존하고, 각자 상대편 컬럼은 NaN이어야 한다."""
    def fake(fromdate, todate, market, investor):
        if market == "KOSPI":
            if investor == "기관합계":
                return _krx_df(["AAA"], [1.0e8])
            return _krx_df(["BBB"], [2.0e8])
        # KOSDAQ: 양쪽 투자자 모두 동일 티커 (빈 응답 방지용, 이 테스트의 관심사 아님)
        return _krx_df(["380540"], [3.0e8])

    with patch("pykrx.stock.get_market_net_purchases_of_equities_by_ticker",
               side_effect=fake):
        df = crawler.crawl_krx_investor_flows("20260713", "20260717")

    kospi = df[df["시장"] == "KOSPI"]
    assert set(kospi["티커"]) == {"AAA", "BBB"}

    row_a = kospi[kospi["티커"] == "AAA"].iloc[0]
    assert row_a["1주기관매매"] == pytest.approx(1.0)
    assert pd.isna(row_a["1주외국인매매"])

    row_b = kospi[kospi["티커"] == "BBB"].iloc[0]
    assert pd.isna(row_b["1주기관매매"])
    assert row_b["1주외국인매매"] == pytest.approx(2.0)


def _minimal_market_df():
    """crawl_naver_market 최소 mock 반환치 (collect_all 파이프라인 통과용)"""
    return pd.DataFrame({
        "티커": ["005930"],
        "종목명": ["삼성전자"],
        "시가총액(억)": [100.0],
    })


def test_collect_all_falls_back_to_naver_on_krx_failure():
    """crawl_krx_investor_flows가 실패하면 실제 collect_all() 경로에서
    flow_source=naver 로 폴백하고 krx_flows가 빈 DataFrame이어야 한다."""
    config = {"kis": {"app_key": "", "app_secret": ""}}
    with (
        patch("modules.crawler.crawl_naver_market", return_value=_minimal_market_df()),
        patch("modules.crawler.build_sector_map", return_value={}),
        patch("modules.crawler.collect_kis_investor_rank", return_value={}),
        patch("modules.crawler.collect_kis_market_info", return_value={}),
        patch("modules.crawler.crawl_period_returns_all", return_value=pd.DataFrame()),
        patch("modules.crawler.crawl_naver_stock_details", return_value=pd.DataFrame()),
        patch("modules.crawler.crawl_krx_investor_flows",
              side_effect=RuntimeError("KRX 순매수 빈 응답")),
    ):
        result = crawler.collect_all(config)

    assert result["flow_source"] == "naver"
    assert result["krx_flows"].empty


def test_collect_all_uses_krx_on_success():
    """crawl_krx_investor_flows가 정상 DataFrame을 반환하면 실제 collect_all() 경로에서
    flow_source=krx 이고 krx_flows가 그 결과를 그대로 담아야 한다."""
    config = {"kis": {"app_key": "", "app_secret": ""}}
    krx_df = pd.DataFrame({
        "티커": ["005930"],
        "시장": ["KOSPI"],
        "1주기관매매": [1.5],
        "1주외국인매매": [-2.0],
    })
    with (
        patch("modules.crawler.crawl_naver_market", return_value=_minimal_market_df()),
        patch("modules.crawler.build_sector_map", return_value={}),
        patch("modules.crawler.collect_kis_investor_rank", return_value={}),
        patch("modules.crawler.collect_kis_market_info", return_value={}),
        patch("modules.crawler.crawl_period_returns_all", return_value=pd.DataFrame()),
        patch("modules.crawler.crawl_naver_stock_details", return_value=pd.DataFrame()),
        patch("modules.crawler.crawl_krx_investor_flows", return_value=krx_df),
    ):
        result = crawler.collect_all(config)

    assert result["flow_source"] == "krx"
    pd.testing.assert_frame_equal(result["krx_flows"], krx_df)
