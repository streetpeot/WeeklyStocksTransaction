"""발행기 — 파이프라인 성공 후 볼트 반입·PDF·텔레그램 전송을 오케스트레이션한다.

수동 실행: python3 -m modules.publisher --date 20260710 [--dm] [--dry-run]
"""
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def _krx_trading_days(start: str, end: str) -> list[str]:
    """KRX 캘린더 기준 거래일 목록(YYYYMMDD). 네트워크 실패 시 예외 전파."""
    from pykrx import stock  # 지연 import — 테스트에서 mock 대상
    df = stock.get_index_ohlcv_by_date(start, end, "1001")  # KOSPI 지수
    return [d.strftime("%Y%m%d") for d in df.index]


def _calendar_weekdays(start_dt: datetime, end_dt: datetime) -> list[str]:
    days, d = [], start_dt
    while d <= end_dt:
        if d.weekday() < 5:
            days.append(d.strftime("%Y%m%d"))
        d += timedelta(days=1)
    return days


def compute_week_range(base_date: str) -> str:
    base = datetime.strptime(base_date, "%Y%m%d")
    monday = base - timedelta(days=base.weekday())
    try:
        days = _krx_trading_days(monday.strftime("%Y%m%d"), base_date)
    except Exception as e:
        logger.warning(f"KRX 거래일 조회 실패({e}) → 달력 폴백")
        days = _calendar_weekdays(monday, base)
    if not days:
        days = [base_date]
    return days[0] if days[0] == days[-1] else f"{days[0]}~{days[-1][4:]}"


def dest_name_for(base_date: str, prefix: str) -> str:
    return f"{prefix}_{compute_week_range(base_date)}"
