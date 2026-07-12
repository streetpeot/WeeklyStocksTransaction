"""
main.py - 진입점
실행 방법:
  python main.py              # 스케줄러 모드 (레거시, launchd 사용 권장)
  python main.py --run-now    # 즉시 실행 (금요일 마감 기준 주간 보고서)
  python main.py --midweek    # 즉시 실행 (주중 WTD 중간 수급동향)
"""

import logging
import sys
from pathlib import Path
from typing import Optional

import pandas as pd
import yaml

# ─────────────────────────────────────────
# 로깅 설정
# ─────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("pipeline.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────
# 설정 로드
# ─────────────────────────────────────────

# ─────────────────────────────────────────
# DB 이력 기반 enrichment
# ─────────────────────────────────────────

def _enrich_from_db(db, week_date: str, processed: dict) -> dict:
    """DB 이력을 이용해 processed dict 보강:
    - 주간 지수 등락 계산 (직전 주 종가 대비)
    - 2주 / 1개월(4주) / 3개월(12주) 기관/외국인 매매 누적 합산
    """
    n_weeks = db.get_week_count()         # 지수 이력 주 수 (주간등락 계산용)
    n_stock_weeks = db.get_stock_week_count()  # 종목별 투자자 이력 주 수 (다기간 매매 계산용)
    processed["n_weeks_in_db"] = n_weeks
    processed["n_stock_weeks_in_db"] = n_stock_weeks

    # ── 주간 지수 등락 (직전 주 종가 대비) ──
    market_info = processed.get("market_info", {})
    for market in ["KOSPI", "KOSDAQ"]:
        idx = market_info.get(f"{market}_index", {})
        cur_close = idx.get("종가")
        if cur_close:
            prev_close = db.get_prev_index_close(market, week_date)
            if prev_close:
                idx["주간등락(p)"] = round(cur_close - prev_close, 2)
                idx["주간등락률(%)"] = round((cur_close - prev_close) / prev_close * 100, 2)
                market_info[f"{market}_index"] = idx

    # ── 다기간 기관/외국인 매매 누적 ──
    # weekly_stock 이력 기준으로 가용 기간 판단 (첫 주 이후부터 2주 누적 가능)
    PERIOD_MAP = [
        (2,  "2주기관매매",  "2주외국인매매"),
        (4,  "1개월기관매매", "1개월외국인매매"),
        (12, "3개월기관매매", "3개월외국인매매"),
    ]
    for market in ["KOSPI", "KOSDAQ"]:
        df = processed.get(market.lower(), pd.DataFrame())
        if df.empty:
            continue
        for n, inst_col, fore_col in PERIOD_MAP:
            if n_stock_weeks < n:
                continue
            acc = db.get_stock_investor_accumulate(market, n)
            if acc.empty:
                continue
            acc = acc.rename(columns={
                "ticker": "티커",
                "inst_net_sum": inst_col,
                "foreign_net_sum": fore_col,
            })
            df = df.merge(acc[["티커", inst_col, fore_col]], on="티커", how="left")
        processed[market.lower()] = df

    return processed


def load_config(path: str = "config.yaml") -> dict:
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"설정 파일 없음: {config_path.resolve()}")
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ─────────────────────────────────────────
# 메인 파이프라인
# ─────────────────────────────────────────

def run_pipeline(config: dict, midweek: bool = False):
    """전체 파이프라인 실행
    midweek=True: 이번 주 월~오늘까지의 중간 수급동향 분석 (--run-now 용)
    """
    logger.info("=" * 60)
    if midweek:
        logger.info("중간 수급동향 파이프라인 시작 (이번 주 ~ 오늘)")
    else:
        logger.info("주간 주가 자금동향 파이프라인 시작")
    logger.info("=" * 60)

    from modules import crawler, processor, database, exporter, visualizer, reporter
    from modules.processor import detect_rotation

    # [1] 데이터 수집
    logger.info("[1/6] 데이터 수집 (KIS + pykrx + 네이버금융)...")
    raw = crawler.collect_all(config, midweek=midweek)

    # [2] 데이터 가공
    logger.info("[2/6] 데이터 가공 (파생 컬럼 + 섹터 집계)...")
    processed = processor.process(raw)

    # [3] DB 저장
    logger.info("[3/6] SQLite DB 저장...")
    db_path = config.get("output", {}).get("history_db", "./history.db")
    db = database.Database(db_path)
    week_date = processed.get("week_date", processed.get("base_date", ""))
    max_weeks = config.get("output", {}).get("max_weeks", 52)
    database.upsert_week(db, week_date, processed, max_weeks=max_weeks)

    # DB 이력 기반 enrichment: 주간등락 + 다기간 기관/외국인 매매 누적
    processed = _enrich_from_db(db, week_date, processed)
    n_weeks = processed.get("n_weeks_in_db", 1)
    logger.info(
        f"  DB 누적 이력: {n_weeks}주 | "
        f"2주{'✓' if n_weeks>=2 else '✗'} "
        f"1개월{'✓' if n_weeks>=4 else '✗'} "
        f"3개월{'✓' if n_weeks>=12 else '✗'}"
    )

    # [4] Excel 저장
    logger.info("[4/6] Excel 저장...")
    excel_path = exporter.save_excel(processed, config)
    logger.info(f"  → {excel_path}")

    # [5] 차트 생성
    logger.info("[5/6] 차트 PNG 생성...")
    chart_paths = visualizer.generate_charts(processed, db, config)
    for k, v in chart_paths.items():
        logger.info(f"  → {v}")

    # 섹터 로테이션 감지 (DB 이력 기반)
    rotation_data = {}
    for market in ["KOSPI", "KOSDAQ"]:
        sec_hist = db.get_sector_history(market, n_weeks=4)
        if not sec_hist.empty:
            rotation_data[market] = detect_rotation(sec_hist)

    # [6] AI 보고서 생성
    logger.info("[6/6] AI 보고서 생성 (claude-sonnet-4-6)...")
    report_path = reporter.generate_report(processed, chart_paths, rotation_data, config)
    logger.info(f"  → {report_path}")

    logger.info("=" * 60)
    logger.info("파이프라인 완료!")
    logger.info(f"  Excel:  {excel_path}")
    logger.info(f"  보고서: {report_path}")
    logger.info(f"  차트:   {len(chart_paths)}개")
    logger.info("=" * 60)


# ─────────────────────────────────────────
# 진입점
# ─────────────────────────────────────────

if __name__ == "__main__":
    config = load_config()

    if "--midweek" in sys.argv:
        # 주중 WTD 중간 수급동향 (월~목 수동 실행 시)
        run_pipeline(config, midweek=True)
    elif "--run-now" in sys.argv:
        # 즉시 실행 — 금요일 마감 기준 주간 보고서
        # (crawler가 weekday를 보고 base_date를 last_friday로 자동 정렬)
        run_pipeline(config, midweek=False)
    else:
        # 스케줄러 시작
        from modules.scheduler import create_scheduler

        logger.info("스케줄러 모드로 시작합니다. (Ctrl+C로 종료)")
        scheduler = create_scheduler(
            pipeline_func=lambda: run_pipeline(config),
            schedule_config=config.get("schedule", {}),
        )
        try:
            scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            logger.info("스케줄러 종료.")
