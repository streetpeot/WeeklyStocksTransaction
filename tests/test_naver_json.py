"""SJAIINV-197: 네이버 증권 웹페이지 폐지 → m.stock JSON API 로 수집 계층 이전.

네이버가 finance.naver.com 웹페이지를 Npay 증권(stock.naver.com, JavaScript 렌더링)
으로 302 리다이렉트하면서 HTML 파싱이 0건이 됐다(09-11·09-18 2주 연속 실패).
아래 fixture 는 2026-09-19 라이브 응답에서 필요한 필드만 남긴 것이다.
"""
from unittest import mock

import pytest

from modules import crawler


def _stock(code, name, close, diff, ratio, vol, value_won, cap_won, end_type="stock"):
    return {
        "itemCode": code, "stockName": name, "stockEndType": end_type,
        "closePriceRaw": str(close), "compareToPreviousClosePrice": diff,
        "fluctuationsRatio": ratio, "accumulatedTradingVolumeRaw": str(vol),
        "accumulatedTradingValueRaw": str(value_won), "marketValueRaw": str(cap_won),
    }


_SAMSUNG = _stock("005930", "삼성전자", 260000, "7,500", "2.97",
                  15042569, 3915612000000, 1520032438080000)
_HYNIX = _stock("000660", "SK하이닉스", 500000, "-3,000", "-0.60",
                2000000, 1000000000000, 364000000000000)


def test_crawl_naver_market_maps_json_fields_with_won_to_eok_conversion():
    """Raw 필드는 원 단위다 — 시가총액·거래대금은 억원으로 환산한다.
    거래대금은 기존의 (거래량×현재가) 근사가 아니라 API 가 주는 실제 값이다."""
    page = {"totalCount": 2, "stocks": [_SAMSUNG, _HYNIX]}
    with mock.patch.object(crawler, "_naver_json", return_value=page), \
         mock.patch.object(crawler.time, "sleep"):
        df = crawler.crawl_naver_market(0)
    assert list(df["티커"]) == ["005930", "000660"]
    assert list(df["순위"]) == [1, 2]
    s = df.set_index("티커").loc["005930"]
    assert s["종목명"] == "삼성전자"
    assert s["현재가"] == 260000.0
    assert s["등락률_당일"] == pytest.approx(2.97)
    assert s["거래량"] == 15042569.0
    assert s["시가총액(억)"] == pytest.approx(15200324.38, abs=0.01)
    assert s["거래대금(억)"] == pytest.approx(39156.12, abs=0.01)
    assert df.set_index("티커").loc["000660", "등락률_당일"] == pytest.approx(-0.60)


def test_crawl_naver_market_walks_all_pages_from_total_count():
    pages = {
        1: {"totalCount": 150, "stocks": [_stock(f"{i:06d}", f"종목{i}", 1000, "0", "0.00", 1, 1, 1)
                                          for i in range(1, 101)]},
        2: {"totalCount": 150, "stocks": [_stock(f"{i:06d}", f"종목{i}", 1000, "0", "0.00", 1, 1, 1)
                                          for i in range(101, 151)]},
    }
    calls = []

    def fake(path, params=None):
        calls.append((path, params["page"]))
        return pages[params["page"]]

    with mock.patch.object(crawler, "_naver_json", side_effect=fake), \
         mock.patch.object(crawler.time, "sleep"):
        df = crawler.crawl_naver_market(1)
    assert [c[1] for c in calls] == [1, 2]
    assert all("KOSDAQ" in c[0] for c in calls)
    assert len(df) == 150
    assert list(df["순위"])[-1] == 150  # 페이지를 넘어도 순위가 이어진다


def test_crawl_naver_market_raises_explicit_error_on_zero_rows():
    """0건을 KeyError('티커') 로 죽게 두지 않는다 — 2주간 원인 파악을 늦춘 증상이다."""
    with mock.patch.object(crawler, "_naver_json", return_value={"totalCount": 0, "stocks": []}), \
         mock.patch.object(crawler.time, "sleep"):
        with pytest.raises(RuntimeError, match="0건"):
            crawler.crawl_naver_market(0)


# ── 섹터 매핑: 업종 79개의 이름·번호 체계가 기존과 100% 일치함을 DB 대조로 확인했다 ──

def _members(no, name, codes, total=None):
    return {"groupInfo": {"no": no, "name": name},
            "totalCount": total if total is not None else len(codes),
            "stocks": [{"itemCode": c, "stockName": f"종목{c}"} for c in codes]}


def test_build_sector_map_maps_every_member_to_industry_name():
    groups = {"totalCount": 2, "groups": [{"no": 278, "name": "반도체와반도체장비"},
                                          {"no": 294, "name": "통신장비"}]}

    def fake(path, params=None):
        if path == "/stocks/industry":
            return groups
        if path.endswith("/278"):
            return _members(278, "반도체와반도체장비", ["005930", "000660"])
        return _members(294, "통신장비", ["050890"])

    with mock.patch.object(crawler, "_naver_json", side_effect=fake), \
         mock.patch.object(crawler.time, "sleep"):
        m = crawler.build_sector_map()
    assert m == {"005930": "반도체와반도체장비", "000660": "반도체와반도체장비",
                 "050890": "통신장비"}


def test_build_sector_map_pages_through_large_industries():
    """반도체 업종은 171종목(실측) — 한 페이지(100)로는 다 못 받는다."""
    codes = [f"{i:06d}" for i in range(1, 172)]
    calls = []

    def fake(path, params=None):
        if path == "/stocks/industry":
            return {"totalCount": 1, "groups": [{"no": 278, "name": "반도체와반도체장비"}]}
        calls.append(params["page"])
        lo = (params["page"] - 1) * 100
        return _members(278, "반도체와반도체장비", codes[lo:lo + 100], total=171)

    with mock.patch.object(crawler, "_naver_json", side_effect=fake), \
         mock.patch.object(crawler.time, "sleep"):
        m = crawler.build_sector_map()
    assert calls == [1, 2]
    assert len(m) == 171


def test_build_sector_map_skips_failed_industry_and_continues():
    def fake(path, params=None):
        if path == "/stocks/industry":
            return {"totalCount": 2, "groups": [{"no": 1, "name": "실패업종"},
                                                {"no": 2, "name": "정상업종"}]}
        if path.endswith("/1"):
            raise RuntimeError("HTTP 500")
        return _members(2, "정상업종", ["111111"])

    with mock.patch.object(crawler, "_naver_json", side_effect=fake), \
         mock.patch.object(crawler.time, "sleep"):
        assert crawler.build_sector_map() == {"111111": "정상업종"}


def test_build_sector_map_returns_empty_when_industry_list_fails():
    """기존 계약 유지 — 목록 실패 시 {} (섹터 없이도 파이프라인은 계속된다)."""
    with mock.patch.object(crawler, "_naver_json", side_effect=RuntimeError("down")):
        assert crawler.build_sector_map() == {}


# ── 상위 200 상세: integration(지표·투자자 매매) + finance/annual(연간 재무) ──

_INTEGRATION = {
    "itemCode": "005930", "industryCode": "278",
    "totalInfos": [
        {"code": "per", "key": "PER", "value": "11.66배"},
        {"code": "pbr", "key": "PBR", "value": "3.02배"},
        {"code": "dividendYieldRatio", "key": "배당수익률", "value": "0.64%"},
        {"code": "foreignRate", "key": "외인소진율", "value": "46.48%"},
    ],
    "dealTrendInfos": [  # 최신 거래일이 먼저 온다 (실측)
        {"bizdate": "20260918", "foreignerPureBuyQuant": "-1,673,323", "organPureBuyQuant": "+2,746,972"},
        {"bizdate": "20260917", "foreignerPureBuyQuant": "+1,000,000", "organPureBuyQuant": "-500,000"},
        {"bizdate": "20260916", "foreignerPureBuyQuant": "+100", "organPureBuyQuant": "+200"},
    ],
}

_FINANCE = {"financeInfo": {
    "trTitleList": [
        {"isConsensus": "N", "title": "2023.12.", "key": "202312"},
        {"isConsensus": "N", "title": "2024.12.", "key": "202412"},
        {"isConsensus": "N", "title": "2025.12.", "key": "202512"},
        {"isConsensus": "Y", "title": "2026.12.", "key": "202612"},
    ],
    "rowList": [
        {"title": "매출액", "columns": {"202412": {"value": "3,008,709"}, "202512": {"value": "3,336,059"},
                                       "202612": {"value": "7,362,012"}}},
        {"title": "영업이익", "columns": {"202412": {"value": "327,260"}, "202512": {"value": "436,011"},
                                        "202612": {"value": "3,877,836"}}},
        {"title": "영업이익률", "columns": {"202512": {"value": "13.07"}}},
        {"title": "ROE", "columns": {"202512": {"value": "10.85"}, "202612": {"value": "56.17"}}},
        {"title": "부채비율", "columns": {"202512": {"value": "29.94"}, "202612": {"value": "-"}}},
    ]}}


def test_parse_integration_extracts_ratios_and_strips_units():
    out = crawler._parse_naver_integration(_INTEGRATION, cur_price=260000.0, investor_days=2)
    assert out["PER_NAVER"] == pytest.approx(11.66)
    assert out["PBR_NAVER"] == pytest.approx(3.02)
    assert out["배당수익률_NAVER"] == pytest.approx(0.64)


def test_parse_integration_sums_latest_n_days_of_flows_in_eok():
    """최신 investor_days 일만 합산하고, 주(株)×현재가/1억 으로 억원 환산한다 (기존 의미론)."""
    out = crawler._parse_naver_integration(_INTEGRATION, cur_price=260000.0, investor_days=2)
    assert out["1주외국인매매"] == pytest.approx((-1673323 + 1000000) * 260000 / 1e8, abs=0.01)
    assert out["1주기관매매"] == pytest.approx((2746972 - 500000) * 260000 / 1e8, abs=0.01)


def test_parse_integration_without_price_omits_flows():
    out = crawler._parse_naver_integration(_INTEGRATION, cur_price=None, investor_days=5)
    assert "1주외국인매매" not in out and "1주기관매매" not in out


def test_parse_finance_uses_latest_confirmed_year_not_consensus():
    """추정치(isConsensus=Y) 열을 최근 실적으로 쓰면 안 된다 — 2026E 매출 7.3조가 섞인다."""
    out = crawler._parse_naver_finance(_FINANCE)
    assert out["연간_매출액"] == pytest.approx(3336059.0)
    assert out["연간_영업이익"] == pytest.approx(436011.0)
    assert out["영업이익률"] == pytest.approx(13.07)
    assert out["연간_ROE"] == pytest.approx(10.85)
    assert out["부채비율"] == pytest.approx(29.94)
    assert out["매출액_증가율"] == pytest.approx((3336059 - 3008709) / 3008709 * 100, abs=0.01)
    assert out["영업이익_증가율"] == pytest.approx((436011 - 327260) / 327260 * 100, abs=0.01)


def test_parse_finance_tolerates_missing_rows_and_dash_values():
    out = crawler._parse_naver_finance({"financeInfo": {
        "trTitleList": [{"isConsensus": "N", "key": "202512"}],
        "rowList": [{"title": "부채비율", "columns": {"202512": {"value": "-"}}}]}})
    assert out.get("부채비율") is None
    assert "매출액_증가율" not in out


def test_crawl_details_merges_both_endpoints_and_survives_per_ticker_failure():
    def fake(path, params=None):
        if "999999" in path:
            raise RuntimeError("HTTP 404")
        return _FINANCE if path.endswith("/finance/annual") else _INTEGRATION

    with mock.patch.object(crawler, "_naver_json", side_effect=fake), \
         mock.patch.object(crawler.time, "sleep"):
        df = crawler.crawl_naver_stock_details(["005930", "999999"], {"005930": 260000.0}, n=200)
    assert list(df["티커"]) == ["005930", "999999"]      # 실패 종목도 행은 남는다 (기존 계약)
    ok = df.set_index("티커").loc["005930"]
    assert ok["PBR_NAVER"] == pytest.approx(3.02)
    assert ok["연간_ROE"] == pytest.approx(10.85)


def test_collect_all_fills_per_from_details_stage():
    """구형 전종목 표가 주던 PER 이 JSON 대량 조회에는 없다 — 상위 200 상세에서 채운다."""
    import pandas as pd
    market = pd.DataFrame({"티커": ["005930"], "종목명": ["삼성전자"],
                           "현재가": [260000.0], "시가총액(억)": [100.0]})
    details = pd.DataFrame({"티커": ["005930"], "PER_NAVER": [11.66], "PBR_NAVER": [3.02]})
    with mock.patch.object(crawler, "crawl_naver_market", return_value=market), \
         mock.patch.object(crawler, "build_sector_map", return_value={}), \
         mock.patch.object(crawler, "collect_kis_market_info", return_value={}), \
         mock.patch.object(crawler, "crawl_period_returns_all", return_value=pd.DataFrame()), \
         mock.patch.object(crawler, "crawl_naver_stock_details", return_value=details), \
         mock.patch.object(crawler, "crawl_kis_investor_flows", side_effect=RuntimeError("x")), \
         mock.patch.object(crawler, "crawl_kis_etf_flows", side_effect=RuntimeError("x")), \
         mock.patch.object(crawler, "_week_biz_days", return_value=5):   # 실제 KIS 휴장일 조회 차단
        result = crawler.collect_all({"kis": {"app_key": "", "app_secret": ""}})
    row = result["kospi"].set_index("티커").loc["005930"]
    assert row["PER"] == pytest.approx(11.66)
    assert row["PBR"] == pytest.approx(3.02)
    assert "PER_NAVER" not in result["kospi"].columns
