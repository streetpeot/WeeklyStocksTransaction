"""발행기 — 파이프라인 성공 후 볼트 반입·PDF·텔레그램 전송을 오케스트레이션한다.

수동 실행: python3 -m modules.publisher --date 20260710 [--dm] [--dry-run]
"""
import logging
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests

from modules import notifier, pdf_export

logger = logging.getLogger(__name__)

SENTINEL_PATH = Path(__file__).resolve().parent.parent / "data" / "last_private_pdf.txt"


def _kis_headers() -> dict:
    """KIS 인증 헤더 — 자격증명은 키체인에서 (진입점 무관, SJAIINV-52 원칙)."""
    from modules import secrets
    from modules.crawler import KISClient

    app_key, app_secret = secrets.get_kis_credentials()
    headers = KISClient(app_key, app_secret)._headers("CTCA0903R")
    headers["custtype"] = "P"
    return headers


def _kis_open_days(start: str, end: str) -> list[str]:
    """KIS 휴장일 조회(CTCA0903R)로 [start, end] 구간 개장일 목록(YYYYMMDD).

    SJAIINV-63 에서 도입했다. 당시 KRX 지수 조회의 반복 실패는 시간대성 장애가 아니라
    KRX 의 자동화 탐지에 걸린 것으로 보인다 (SJAIINV-199 에서 정정).

    ⚠️ 판정 필드는 opnd_yn(개장일). tr_day_yn 은 토요일도 'Y' 라(08-29 실측)
    쓰면 토요일이 주간 범위에 들어간다.
    """
    from modules.crawler import KISClient

    r = requests.get(
        f"{KISClient.BASE}/uapi/domestic-stock/v1/quotations/chk-holiday",
        headers=_kis_headers(),
        params={"BASS_DT": start, "CTX_AREA_NK": "", "CTX_AREA_FK": ""},
        timeout=15)
    d = r.json()
    if d.get("rt_cd") != "0":
        raise RuntimeError(f"KIS 휴장일 조회 실패: {d.get('msg1', '').strip()}")
    days = [row["bass_dt"] for row in d.get("output", [])
            if row.get("opnd_yn") == "Y" and start <= row.get("bass_dt", "") <= end]
    if not days:
        raise RuntimeError(f"KIS 휴장일 조회 빈 응답 ({start}~{end})")
    return days


def _open_days(start: str, end: str) -> list[str]:
    """개장일 목록 — KIS 휴장일 API. 실패하면 예외 전파(호출자가 달력으로 폴백).

    예전의 2순위였던 KRX 스크래핑 계층은 제거했다 — KRX 가 자동화 수집을 IP 제한으로
    제재한다 (SJAIINV-199).
    """
    return _kis_open_days(start, end)


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
        days = _open_days(monday.strftime("%Y%m%d"), base_date)
    except Exception as e:
        logger.warning(
            f"거래일 조회 실패({e}) → 달력 폴백. "
            "주간 범위는 월~금 전체를 쓴 **추정**이며 휴장일이 섞일 수 있다 "
            "— 보고서 제목·볼트 파일명이 실제 거래일과 다를 수 있음")
        days = _calendar_weekdays(monday, base)
    if not days:
        days = [base_date]
    if days and days[-1] != base_date:
        logger.warning(f"주간 범위 끝({days[-1]})이 기준일({base_date})과 다름 — 공휴일 또는 데이터 지연 가능")
    return days[0] if days[0] == days[-1] else f"{days[0]}~{days[-1][4:]}"


def dest_name_for(base_date: str, prefix: str) -> str:
    return f"{prefix}_{compute_week_range(base_date)}"


def _base_date_from(report_path: Path) -> str:
    m = re.search(r"_(\d{8})\.md$", str(report_path))
    if not m:
        raise ValueError(f"보고서 파일명에서 기준일을 찾지 못함: {report_path}")
    return m.group(1)


def publish(config: dict, report_path, *, personal_path=None, to_dm: bool = False,
            dry_run: bool = False, private_pdf: bool = False) -> list[str]:
    """발행 파이프라인 오케스트레이션: 볼트 반입 → PDF 변환 → 텔레그램 전송.

    라우팅: 볼트·DM=개인용(없으면 볼트는 공유용), 채널=공유용.

    파이프라인 단계 실패는 반환 리스트로 보고하지만, report_path 파일명이
    `_YYYYMMDD.md` 패턴이 아니면 ValueError를 raise한다 (호출자[main.py 훅]이 감쌀 것).

    Args:
        config: 설정 딕셔너리 (publish·output 키 필수)
        report_path: 보고서 markdown 파일 경로 (공유용 — name 계산 기준)
        personal_path: 개인용(워치리스트 포함) 보고서 경로. 있으면 볼트 반입 대상과
            DM 전송 대상이 개인용으로 바뀐다. None이면 현행과 동일(볼트=공유용, DM 없음).
        to_dm: True면 telegram_channel 대신 notify_chat_id로 전송 (공유용 PDF 리허설)
        dry_run: True면 파이프라인 스킵
        private_pdf: True면 볼트 반입 후 PDF만 생성하고 텔레그램 전송은 전부 스킵,
            PDF 절대경로를 SENTINEL_PATH(<repo>/data/last_private_pdf.txt)에 기록한다.

    Returns:
        에러 메시지 리스트 (성공 시 빈 리스트)

    Raises:
        ValueError: report_path 파일명에서 기준일(_YYYYMMDD.md) 추출 불가
    """
    pub = config.get("publish", {})
    report_path = Path(report_path)
    errors: list[str] = []
    # 경고 ≠ 오류 (SJAIINV-64). vault_ingest exit 2 는 계약상 「반입 성공 + lint 경고」이고,
    # lint 는 볼트 전체를 검사하므로 위반이 반입 파일 것이 아닐 수 있다(08-28 실측:
    # 위반 전부 타 세션 파일). 오류로 섞으면 「발행 완료」가 억제돼 사후 판독이 틀어진다.
    warnings: list[str] = []
    base_date = _base_date_from(report_path)
    name = dest_name_for(base_date, pub.get("title_prefix", "국내증시 자금동향"))
    chart_glob = str(Path(config["output"].get("chart_dir", "./data/charts")) / f"*_{base_date}.png")
    ingest_src = Path(personal_path) if personal_path else report_path

    if dry_run:
        logger.info(f"[dry-run] dest={name}, charts={chart_glob}")
        return []

    # ① 볼트 반입 (독립 — 실패해도 계속) — 개인용 우선, 없으면 공유용
    try:
        r = subprocess.run(
            [sys.executable, pub["vault_ingest"],
             "--doc-type", "수급동향", "--file", str(ingest_src),
             "--dest-name", name, "--assets", chart_glob],
            capture_output=True, text=True, timeout=600)
        if r.returncode == 2 and "반입 완료" in r.stdout:
            warnings.append(
                "볼트 전체 lint 경고 — 반입 파일과 무관할 수 있음(볼트 전수 검사): "
                f"{r.stderr.strip()[-300:]}")
        elif r.returncode != 0:
            errors.append(f"볼트 반입 실패: {r.stderr.strip()[-300:]}")
    except Exception as e:
        errors.append(f"볼트 반입 실행 실패: {e}")

    # ② PDF → ③ 전송 (PDF 실패 시 전송만 스킵) — 채널은 항상 공유용
    if private_pdf:
        # 비공개 PDF 모드: 볼트 반입은 위에서 이미 수행, 전송은 전부 스킵, PDF만 생성.
        src = Path(personal_path) if personal_path else report_path  # 개인용(워치리스트 포함) 우선
        try:
            pdf = pdf_export.md_to_pdf(src, report_path.parent / "pdf" / f"{name}_개인.pdf")
            SENTINEL_PATH.parent.mkdir(parents=True, exist_ok=True)
            SENTINEL_PATH.write_text(str(pdf), encoding="utf-8")
        except Exception as e:
            errors.append(f"비공개 PDF 생성 실패: {e}")
        return errors

    if pub.get("pdf_enabled", True):
        try:
            pdf = pdf_export.md_to_pdf(report_path, report_path.parent / "pdf" / f"{name}.pdf")
            try:
                chat = pub["notify_chat_id"] if to_dm else pub["telegram_channel"]
                notifier.send_document(chat, pdf, caption=name)
            except Exception as e:
                errors.append(f"전송 실패: {e}")
        except Exception as e:
            errors.append(f"PDF 변환 실패: {e}")

        # 개인용 → DM (워치리스트 포함본, 있을 때만 — 다른 단계와 독립)
        if personal_path:
            try:
                pdf_p = pdf_export.md_to_pdf(
                    Path(personal_path),
                    report_path.parent / "pdf" / f"{name}_개인.pdf")
                notifier.send_document(pub["notify_chat_id"], pdf_p,
                                       caption=f"{name} · 워치리스트")
            except Exception as e:
                errors.append(f"개인용 DM 전송 실패: {e}")
    else:
        logger.info("pdf_enabled=false — PDF 변환·전송 생략")

    # ④ 통지 (통지 실패는 삼킨다 — 로그만)
    if errors:
        try:
            notifier.send_message(pub["notify_chat_id"],
                                  "⚠️ WST 발행 경고\n" + "\n".join(f"- {e}" for e in errors))
        except Exception as e:
            logger.error(f"통지 실패(무시): {e}")
    elif warnings:
        # 가시성은 유지한다 — 조용히 삼키면 TelegramDigest 의 「사라진 알림」 꼴이 된다.
        try:
            notifier.send_message(pub["notify_chat_id"],
                                  "ℹ️ WST 발행 정상 — 참고 경고\n"
                                  + "\n".join(f"- {w}" for w in warnings))
        except Exception as e:
            logger.error(f"통지 실패(무시): {e}")
    for e in errors:
        logger.error(e)
    for w in warnings:
        logger.warning(w)
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
    from modules.log_setup import setup_logging

    setup_logging()
    _main()
