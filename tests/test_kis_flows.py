"""SJAIINV-199: 수급 데이터를 KRX 스크래핑(pykrx)에서 KIS 공식 API 로 이전.

KRX 는 2026-09-19 에 이 머신의 IP 를 "자동화 수단을 통한 비정상 대량 조회"로 1일간
제한했다(이용약관 제10조 제2호). KIS FHKST01010900 은 종목별 최근 30거래일의
외국인·기관·개인 순매수를 준다. 09-04 주 대조: 기관은 KRX 기반 DB 값과 소수점까지
일치, 외국인은 0.4~2% 차이(KIS 값에 기타외국인이 포함된 것으로 보인다).
"""
from unittest import mock

import pytest

from modules import crawler


def _day(date, inst, fore, indiv="0"):
    return {"stck_bsop_date": date, "orgn_ntby_tr_pbmn": inst,
            "frgn_ntby_tr_pbmn": fore, "prsn_ntby_tr_pbmn": indiv}


# 2026-09-19 라이브 응답(삼성전자)에서 발췌 — 단위는 백만원
_ROWS = [
    _day("20260918", "715415", "-434923"),
    _day("20260917", "50959", "-550702"),
    _day("20260914", "-454331", "-867842"),
    _day("20260911", "-570441", "-911722"),   # 전 주 — 범위 밖
]


def test_fetch_kis_investor_daily_returns_rows_and_checks_rt_cd():
    kis = mock.Mock()
    kis.get.return_value = {"rt_cd": "0", "output": _ROWS}
    assert crawler.fetch_kis_investor_daily(kis, "005930") == _ROWS
    tr, path, params = kis.get.call_args[0]
    assert tr == "FHKST01010900" and path.endswith("/inquire-investor")
    assert params == {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "005930"}

    kis.get.return_value = {"rt_cd": "1", "msg1": "없는 종목"}
    with pytest.raises(RuntimeError, match="없는 종목"):
        crawler.fetch_kis_investor_daily(kis, "999999")


def test_sum_kis_flows_converts_million_won_to_eok_within_range():
    """tr_pbmn 은 백만원 → /100 = 억원. 범위 밖 날짜는 합산하지 않는다."""
    out = crawler._sum_kis_flows(_ROWS, "20260914", "20260918")
    assert out["1주기관매매"] == pytest.approx((715415 + 50959 - 454331) / 100)
    assert out["1주외국인매매"] == pytest.approx((-434923 - 550702 - 867842) / 100)


def test_sum_kis_flows_returns_none_when_no_day_in_range():
    assert crawler._sum_kis_flows(_ROWS, "20260801", "20260805") is None


def test_sum_kis_flows_tolerates_blank_values():
    rows = [_day("20260918", "", "100")]
    out = crawler._sum_kis_flows(rows, "20260914", "20260918")
    assert out == {"1주기관매매": 0.0, "1주외국인매매": 1.0}


def test_crawl_kis_investor_flows_builds_same_shape_as_the_krx_frame():
    kis = mock.Mock()
    with mock.patch.object(crawler, "fetch_kis_investor_daily", return_value=_ROWS), \
         mock.patch.object(crawler.time, "sleep"):
        df = crawler.crawl_kis_investor_flows(
            kis, {"KOSPI": ["005930"], "KOSDAQ": ["247540"]}, "20260914", "20260918")
    assert list(df.columns) == ["티커", "시장", "1주기관매매", "1주외국인매매"]
    assert set(zip(df["티커"], df["시장"])) == {("005930", "KOSPI"), ("247540", "KOSDAQ")}


def test_crawl_kis_investor_flows_skips_failed_tickers():
    def fake(kis, ticker):
        if ticker == "BAD000":
            raise RuntimeError("조회 실패")
        return _ROWS

    with mock.patch.object(crawler, "fetch_kis_investor_daily", side_effect=fake), \
         mock.patch.object(crawler.time, "sleep"):
        df = crawler.crawl_kis_investor_flows(
            mock.Mock(), {"KOSPI": ["005930", "BAD000"]}, "20260914", "20260918")
    assert list(df["티커"]) == ["005930"]


def test_crawl_kis_investor_flows_raises_when_everything_fails():
    """빈 결과 = 실패 (리포 관례) — 호출자가 Naver 상위 200 폴백을 결정한다."""
    with mock.patch.object(crawler, "fetch_kis_investor_daily", side_effect=RuntimeError("down")), \
         mock.patch.object(crawler.time, "sleep"):
        with pytest.raises(RuntimeError, match="빈 응답"):
            crawler.crawl_kis_investor_flows(mock.Mock(), {"KOSPI": ["005930"]},
                                             "20260914", "20260918")


def test_fetch_retries_once_on_kis_rate_limit():
    """EGW00201(초당 거래건수 초과)은 잠깐 쉬고 다시 부르면 된다."""
    kis = mock.Mock()
    kis.get.side_effect = [
        {"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수를 초과하였습니다."},
        {"rt_cd": "0", "output": _ROWS},
    ]
    with mock.patch.object(crawler.time, "sleep") as slept:
        assert crawler.fetch_kis_investor_daily(kis, "005930") == _ROWS
    assert kis.get.call_count == 2 and slept.called


# ── ETF 수급: 유니버스·순위는 네이버 JSON 목록, 수급은 KIS ──

def _etf_universe():
    import pandas as pd
    return pd.DataFrame({
        "티커": ["069500", "360750", "133690"],
        "종목명": ["KODEX 200", "TIGER 미국S&P500", "TIGER 미국나스닥100"],
        "거래대금(억)": [5000.0, 9000.0, 100.0],
    })


def _etf_rows():
    return [_day("20260918", "1000", "-2000", "500"), _day("20260917", "3000", "1000", "-700")]


def test_crawl_kis_etf_flows_takes_top_n_by_trading_value():
    seen = []

    def fake(kis, ticker):
        seen.append(ticker)
        return _etf_rows()

    with mock.patch.object(crawler, "fetch_kis_investor_daily", side_effect=fake), \
         mock.patch.object(crawler.time, "sleep"):
        flows, agg = crawler.crawl_kis_etf_flows(mock.Mock(), _etf_universe(),
                                                 "20260914", "20260918", top_n=2)
    assert seen == ["360750", "069500"]          # 거래대금 내림차순 상위 2
    assert list(flows.columns) == ["티커", "종목명", "1주기관매매", "1주외국인매매"]
    row = flows.set_index("티커").loc["069500"]
    assert row["종목명"] == "KODEX 200"
    assert row["1주기관매매"] == pytest.approx(40.0) and row["1주외국인매매"] == pytest.approx(-10.0)


def test_crawl_kis_etf_flows_aggregate_is_labeled_as_top_n_not_whole_market():
    """KRX 는 ETF 시장 전체 집계를 1콜로 줬지만 KIS 에는 없다 — 수집한 상위 N 의 합계이며
    그렇게 라벨링한다. 「ETF 시장 전체」로 내보내면 거짓이다."""
    with mock.patch.object(crawler, "fetch_kis_investor_daily", return_value=_etf_rows()), \
         mock.patch.object(crawler.time, "sleep"):
        _, agg = crawler.crawl_kis_etf_flows(mock.Mock(), _etf_universe(),
                                             "20260914", "20260918", top_n=3)
    assert agg["범위"] == "거래대금 상위 3 ETF 합계"
    assert agg["기관"] == pytest.approx(120.0)      # (1000+3000)/100 × 3종목
    assert agg["외국인"] == pytest.approx(-30.0)
    assert agg["개인"] == pytest.approx(-6.0)


def test_crawl_kis_etf_flows_raises_on_empty_universe_or_result():
    import pandas as pd
    with pytest.raises(RuntimeError, match="빈"):
        crawler.crawl_kis_etf_flows(mock.Mock(), pd.DataFrame(), "20260914", "20260918")


def test_etf_section_uses_scope_label_from_aggregate():
    import pandas as pd
    from modules import etf_section
    flows = pd.DataFrame({"티커": ["069500"], "종목명": ["KODEX 200"],
                          "1주기관매매": [40.0], "1주외국인매매": [-10.0]})
    md = etf_section.build_etf_section(
        flows, {"범위": "거래대금 상위 120 ETF 합계", "외국인": -30.0, "기관": 120.0, "개인": -6.0})
    assert "**거래대금 상위 120 ETF 합계**: 외국인 -30억 · 기관 +120억 · 개인 -6억" in md
    assert "ETF 시장 전체" not in md
    assert "KRX" not in md          # 출처 표기도 더 이상 KRX 가 아니다


# ── collect_all 배선 ──

def _market_df(rows):
    import pandas as pd
    return pd.DataFrame(rows, columns=["티커", "종목명", "현재가", "시가총액(억)", "거래대금(억)", "종목유형"])


def _patched_collect_all(kospi, kosdaq, flows_side_effect=None, etf_side_effect=None):
    import pandas as pd
    flows_df = pd.DataFrame({"티커": ["005930"], "시장": ["KOSPI"],
                             "1주기관매매": [1.0], "1주외국인매매": [2.0]})
    etf_df = pd.DataFrame({"티커": ["069500"], "종목명": ["KODEX 200"],
                           "1주기관매매": [3.0], "1주외국인매매": [4.0]})
    patches = [
        mock.patch.object(crawler, "crawl_naver_market", side_effect=[kospi, kosdaq]),
        mock.patch.object(crawler, "build_sector_map", return_value={}),
        mock.patch.object(crawler, "collect_kis_market_info", return_value={}),
        mock.patch.object(crawler, "crawl_period_returns_all", return_value=pd.DataFrame()),
        mock.patch.object(crawler, "crawl_naver_stock_details", return_value=pd.DataFrame()),
        mock.patch.object(crawler, "crawl_kis_investor_flows",
                          side_effect=flows_side_effect, return_value=flows_df),
        mock.patch.object(crawler, "crawl_kis_etf_flows",
                          side_effect=etf_side_effect, return_value=(etf_df, {"범위": "x"})),
        mock.patch.object(crawler, "_week_biz_days", return_value=5),   # 실제 KIS 휴장일 조회 차단
    ]
    return patches


def test_collect_all_sends_only_stocks_to_flows_and_only_etfs_to_etf_crawl():
    kospi = _market_df([("005930", "삼성전자", 260000.0, 100.0, 39156.0, "stock"),
                        ("069500", "KODEX 200", 50000.0, 50.0, 5000.0, "etf")])
    kosdaq = _market_df([("247540", "에코프로비엠", 100000.0, 10.0, 300.0, "stock")])
    patches = _patched_collect_all(kospi, kosdaq)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5] as flows, \
            patches[6] as etf, patches[7]:
        result = crawler.collect_all({"kis": {"app_key": "", "app_secret": ""}})
    tickers_by_market = flows.call_args[0][1]
    assert tickers_by_market == {"KOSPI": ["005930"], "KOSDAQ": ["247540"]}   # ETF 제외
    assert list(etf.call_args[0][1]["티커"]) == ["069500"]                     # ETF 만
    assert result["flow_source"] == "kis"
    assert list(result["investor_flows"]["티커"]) == ["005930"]
    assert result["etf_market_agg"] == {"범위": "x"}


def test_collect_all_falls_back_to_naver_sample_when_kis_flows_fail():
    kospi = _market_df([("005930", "삼성전자", 260000.0, 100.0, 39156.0, "stock")])
    kosdaq = _market_df([("247540", "에코프로비엠", 100000.0, 10.0, 300.0, "stock")])
    patches = _patched_collect_all(kospi, kosdaq, flows_side_effect=RuntimeError("KIS down"),
                                   etf_side_effect=RuntimeError("KIS down"))
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], \
            patches[7]:
        result = crawler.collect_all({"kis": {"app_key": "", "app_secret": ""}})
    assert result["flow_source"] == "naver"
    assert result["investor_flows"].empty
    assert result["etf_flows"].empty and result["etf_market_agg"] is None


def test_crawl_naver_market_carries_stock_end_type():
    page = {"totalCount": 1, "stocks": [{
        "itemCode": "069500", "stockName": "KODEX 200", "stockEndType": "etf",
        "closePriceRaw": "50000", "compareToPreviousClosePrice": "100", "fluctuationsRatio": "0.2",
        "accumulatedTradingVolumeRaw": "10", "accumulatedTradingValueRaw": "500000000000",
        "marketValueRaw": "5000000000000"}]}
    with mock.patch.object(crawler, "_naver_json", return_value=page), \
         mock.patch.object(crawler.time, "sleep"):
        df = crawler.crawl_naver_market(0)
    assert df.loc[0, "종목유형"] == "etf"
