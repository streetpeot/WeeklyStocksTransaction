"""발행기 — 파이프라인 성공 후 볼트 반입·PDF·텔레그램 전송을 오케스트레이션한다.

수동 실행: python3 -m modules.publisher --date 20260710 [--dm] [--dry-run]
"""
import logging
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from modules import notifier, pdf_export

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


def _base_date_from(report_path: Path) -> str:
    m = re.search(r"_(\d{8})\.md$", str(report_path))
    if not m:
        raise ValueError(f"보고서 파일명에서 기준일을 찾지 못함: {report_path}")
    return m.group(1)


def publish(config: dict, report_path, *, to_dm: bool = False, dry_run: bool = False) -> list[str]:
    """발행 파이프라인 오케스트레이션: 볼트 반입 → PDF 변환 → 텔레그램 전송.

    파이프라인 단계 실패는 반환 리스트로 보고하지만, report_path 파일명이
    `_YYYYMMDD.md` 패턴이 아니면 ValueError를 raise한다 (호출자[main.py 훅]이 감쌀 것).

    Args:
        config: 설정 딕셔너리 (publish·output 키 필수)
        report_path: 보고서 markdown 파일 경로
        to_dm: True면 telegram_channel 대신 notify_chat_id로 전송
        dry_run: True면 파이프라인 스킵

    Returns:
        에러 메시지 리스트 (성공 시 빈 리스트)

    Raises:
        ValueError: report_path 파일명에서 기준일(_YYYYMMDD.md) 추출 불가
    """
    pub = config.get("publish", {})
    report_path = Path(report_path)
    errors: list[str] = []
    base_date = _base_date_from(report_path)
    name = dest_name_for(base_date, pub.get("title_prefix", "국내증시 자금동향"))
    chart_glob = str(Path(config["output"].get("chart_dir", "./data/charts")) / f"*_{base_date}.png")

    if dry_run:
        logger.info(f"[dry-run] dest={name}, charts={chart_glob}")
        return []

    # ① 볼트 반입 (독립 — 실패해도 계속)
    try:
        r = subprocess.run(
            [sys.executable, pub["vault_ingest"],
             "--doc-type", "수급동향", "--file", str(report_path),
             "--dest-name", name, "--assets", chart_glob],
            capture_output=True, text=True, timeout=600)
        if r.returncode == 1:
            errors.append(f"볼트 반입 실패: {r.stderr.strip()[-300:]}")
        elif r.returncode == 2:
            errors.append(f"볼트 반입 lint 경고: {r.stderr.strip()[-300:]}")
    except Exception as e:
        errors.append(f"볼트 반입 실행 실패: {e}")

    # ② PDF → ③ 전송 (PDF 실패 시 전송만 스킵)
    try:
        pdf = pdf_export.md_to_pdf(report_path, report_path.parent / "pdf" / f"{name}.pdf")
        try:
            chat = pub["notify_chat_id"] if to_dm else pub["telegram_channel"]
            notifier.send_document(chat, pdf, caption=name)
        except Exception as e:
            errors.append(f"전송 실패: {e}")
    except Exception as e:
        errors.append(f"PDF 변환 실패: {e}")

    # ④ 통지 (실패는 삼킨다 — 로그만)
    if errors:
        try:
            notifier.send_message(pub["notify_chat_id"],
                                  "⚠️ WST 발행 경고\n" + "\n".join(f"- {e}" for e in errors))
        except Exception as e:
            logger.error(f"통지 실패(무시): {e}")
    for e in errors:
        logger.error(e)
    if not errors:
        logger.info(f"발행 완료: {name}")
    return errors


def _main():
    import argparse

    import yaml
    p = argparse.ArgumentParser(description="WST 발행기 수동 실행")
    p.add_argument("--date", required=True, help="기준일 YYYYMMDD")
    p.add_argument("--dm", action="store_true", help="채널 대신 DM으로 전송(리허설)")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    config = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    report = Path(config["output"].get("report_dir", "./data")) / f"주가자금동향_{a.date}.md"
    errs = publish(config, report, to_dm=a.dm, dry_run=a.dry_run)
    sys.exit(1 if any("실패" in e for e in errs) else 0)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    _main()
