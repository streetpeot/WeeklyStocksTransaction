"""SJAIINV-202: 휴장 주간의 거래일 수.

collect_all 정규 경로가 이번 주 거래일 수를 5로 고정했다. fchart 1주 등락률은 봉 개수로
창을 잡기 때문에, 휴장이 낀 주에는 전주 거래일이 창에 들어갔다 — 08-21 발행본의
"삼성전자 주간 +5.04%"는 4거래일 기준으로 +2.55%다. 거래일 수는 발행 제목 범위와
같은 출처(KIS 휴장일 API)로 센다.
"""
from datetime import date
from unittest import mock

import pandas as pd

from modules import crawler, publisher, reporter

# 2026-09-21 주: 추석으로 09-24·25 휴장 (KIS 휴장일 API 실측)
_CHUSEOK_OPEN = ["20260921", "20260922", "20260923"]


class _Chuseok(date):
    """collect_all 이 보는 오늘 — 휴장일인 금요일."""

    @classmethod
    def today(cls):
        return cls(2026, 9, 25)


def test_week_biz_days_counts_open_days_from_the_title_range_source():
    with mock.patch.object(publisher, "_open_days", return_value=_CHUSEOK_OPEN) as od:
        assert crawler._week_biz_days(date(2026, 9, 21), date(2026, 9, 25)) == 3
    od.assert_called_once_with("20260921", "20260925")


def test_week_biz_days_falls_back_to_five_with_warning(caplog):
    """조회 실패는 수정 전 동작(5)으로 되돌아간다 — 실행을 막지 않되 흔적은 남긴다."""
    with mock.patch.object(publisher, "_open_days", side_effect=RuntimeError("KIS down")):
        assert crawler._week_biz_days(date(2026, 9, 21), date(2026, 9, 25)) == 5
    assert "KIS down" in caplog.text


def _market(rows):
    return pd.DataFrame(rows, columns=["티커", "종목명", "현재가", "시가총액(억)", "거래대금(억)", "종목유형"])


def test_collect_all_sizes_week_windows_by_actual_trading_days():
    kospi = _market([("005930", "삼성전자", 260000.0, 100.0, 39156.0, "stock")])
    kosdaq = _market([("247540", "에코프로비엠", 100000.0, 10.0, 300.0, "stock")])
    flows = pd.DataFrame({"티커": ["005930"], "시장": ["KOSPI"],
                          "1주기관매매": [1.0], "1주외국인매매": [2.0]})
    with mock.patch.object(crawler, "date", _Chuseok), \
         mock.patch.object(publisher, "_open_days", return_value=_CHUSEOK_OPEN), \
         mock.patch.object(crawler, "crawl_naver_market", side_effect=[kospi, kosdaq]), \
         mock.patch.object(crawler, "build_sector_map", return_value={}), \
         mock.patch.object(crawler, "collect_kis_market_info", return_value={}), \
         mock.patch.object(crawler, "crawl_period_returns_all",
                           return_value=pd.DataFrame()) as period, \
         mock.patch.object(crawler, "crawl_naver_stock_details",
                           return_value=pd.DataFrame()) as details, \
         mock.patch.object(crawler, "crawl_kis_investor_flows", return_value=flows) as kis_flows, \
         mock.patch.object(crawler, "crawl_kis_etf_flows", side_effect=RuntimeError("x")):
        result = crawler.collect_all({"kis": {"app_key": "", "app_secret": ""}})

    assert period.call_args.kwargs["week_days"] == 3        # 1주 등락률 창: 09-18 종가 → 09-23 종가
    assert details.call_args.kwargs["investor_days"] == 3   # Naver 투자자 합산(폴백 경로)
    assert result["biz_days"] == 3
    assert result["week_start"] == "20260918"               # 직전 주 마지막 평일
    assert result["base_date"] == "20260925"                # 파일명·DB 키는 금요일 관례 유지
    assert kis_flows.call_args[0][2:] == ("20260921", "20260925")   # 수급은 원래 날짜 기반


def test_report_period_label_uses_actual_trading_days():
    ctx = reporter._build_data_context(
        {"base_date": "20260925", "biz_days": 3, "is_midweek": False}, {}, {})
    assert ctx["period_desc"] == "2026년 09월 25일 기준 (3거래일)"
