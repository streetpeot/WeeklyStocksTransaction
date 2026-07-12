from unittest import mock

from modules import publisher


def test_week_range_normal_week():
    # pykrx 성공 경로: 월~금 5거래일을 흉내
    with mock.patch.object(publisher, "_krx_trading_days",
                           return_value=["20260706", "20260707", "20260708", "20260709", "20260710"]):
        assert publisher.compute_week_range("20260710") == "20260706~0710"


def test_week_range_holiday_week():
    with mock.patch.object(publisher, "_krx_trading_days",
                           return_value=["20260303", "20260304"]):
        assert publisher.compute_week_range("20260304") == "20260303~0304"


def test_week_range_single_day():
    with mock.patch.object(publisher, "_krx_trading_days", return_value=["20260710"]):
        assert publisher.compute_week_range("20260710") == "20260710"


def test_week_range_fallback_when_krx_fails():
    with mock.patch.object(publisher, "_krx_trading_days", side_effect=RuntimeError("KRX down")):
        # 2026-07-10 = 금 → 달력 폴백 월(0706)~기준일(0710)
        assert publisher.compute_week_range("20260710") == "20260706~0710"


def test_dest_name_for():
    with mock.patch.object(publisher, "compute_week_range", return_value="20260706~0710"):
        assert publisher.dest_name_for("20260710", "국내증시 자금동향") == "국내증시 자금동향_20260706~0710"
