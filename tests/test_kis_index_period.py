"""KIS 일별 지수 조회의 TR 코드 — 2026-09-19 라이브로 확인한 값.

코드에 있던 FHKUP03500200 은 다른 TR 이라 rt_cd=2
(「ERROR INPUT FIELD NOT FOUND [FID_INPUT_HOUR_1]」)로 항상 빈 목록을 돌려줬다.
이 메서드는 그동안 호출처가 없어 드러나지 않았고, 09-11 주 지수 종가를 백필하려다
발견했다 (SJAIINV-197). 올바른 TR 은 FHKUP03500100(국내주식업종기간별시세)이다.
"""
from unittest import mock

from modules.crawler import KISClient


def test_get_index_period_uses_the_daily_index_chart_tr():
    rows = [{"stck_bsop_date": "20260911", "bstp_nmix_prpr": "6909.91"}]
    kis = KISClient("k", "s")
    with mock.patch.object(KISClient, "get", return_value={"rt_cd": "0", "output2": rows}) as get:
        assert kis.get_index_period("0001", "20260907", "20260911") == rows
    assert get.call_args[0][0] == "FHKUP03500100"
    assert get.call_args[0][2]["FID_INPUT_ISCD"] == "0001"
