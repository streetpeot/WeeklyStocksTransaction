"""
regen_report.py - 기존 Excel + DB 데이터로 보고서만 재생성
Usage: .venv/bin/python regen_report.py [날짜(YYYYMMDD)]
  날짜 생략 시 DB의 최신 week_date 사용
"""

import logging
import sys
from pathlib import Path

import pandas as pd
import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Excel 헤더 → processed 내부 컬럼명 매핑
EXCEL_TO_SRC = {
    "순위":             "순위",
    "티커":             "티커",
    "종목명":           "종목명",
    "시가총액(억)":     "시가총액",
    "거래대금(억)":     "거래대금(억)",
    "PER":              "PER",
    "PBR":              "PBR",
    "배당수익률(%)":    "배당수익률",
    "1주등락률(%)":     "1주등락률",
    "1개월등락률(%)":   "1개월등락률",
    "3개월등락률(%)":   "3개월등락률",
    "6개월등락률(%)":   "6개월등락률",
    "1주기관매매(억)":  "1주기관매매",
    "2주기관매매(억)":  "2주기관매매",
    "1개월기관매매(억)": "1개월기관매매",
    "3개월기관매매(억)": "3개월기관매매",
    "1주외국인매매(억)": "1주외국인매매",
    "2주외국인매매(억)": "2주외국인매매",
    "1개월외국인매매(억)": "1개월외국인매매",
    "3개월외국인매매(억)": "3개월외국인매매",
    "시가대비기관비중(%)":   "시가대비_기관매매비중_1주",
    "시가대비외국인비중(%)": "시가대비_외국인매매비중_1주",
    "섹터":             "섹터",
    "연간매출액(억)":   "연간_매출액",
    "연간영업이익(억)": "연간_영업이익",
    "영업이익률(%)":    "영업이익률",
    "연간ROE(%)":       "연간_ROE",
    "부채비율(%)":      "부채비율",
    "유동비율(%)":      "유동비율",
    "매출액증가율(%)":  "매출액_증가율",
    "영업이익증가율(%)": "영업이익_증가율",
}


def load_config(path="config.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def reconstruct_processed(xlsx_path: str, week_date: str, db) -> dict:
    """Excel + DB → processed dict 재구성"""
    logger.info(f"Excel 로드: {xlsx_path}")

    # ── 1. 종목 DataFrame 읽기 (row1=타이틀 스킵, row2=헤더) ──
    df_kospi  = pd.read_excel(xlsx_path, sheet_name="코스피",  skiprows=1)
    df_kosdaq = pd.read_excel(xlsx_path, sheet_name="코스닥",  skiprows=1)
    df_kospi  = df_kospi.rename(columns=EXCEL_TO_SRC)
    df_kosdaq = df_kosdaq.rename(columns=EXCEL_TO_SRC)

    # ── 2. top_stocks 재계산 ──
    from modules.processor import extract_top_stocks
    top_kospi  = extract_top_stocks(df_kospi)
    top_kosdaq = extract_top_stocks(df_kosdaq)

    # ── 3. cap_weight (Excel 시총비중 탭) ──
    def read_cap_weight(sheet_name):
        df = pd.read_excel(xlsx_path, sheet_name=sheet_name, skiprows=1)
        return df

    cap_kospi  = read_cap_weight("코스피 시총비중")
    cap_kosdaq = read_cap_weight("코스닥 시총비중")

    # ── 4. sector (DB에서 조회) ──
    def db_sector_to_df(market: str):
        hist = db.get_sector_history(market, n_weeks=1)
        if hist.empty:
            return pd.DataFrame()
        df = hist[hist["week_date"] == week_date].copy()
        df = df.rename(columns={
            "sector":          "섹터",
            "stock_count":     "종목수",
            "total_market_cap": "시가총액합(억)",
            "foreign_net":     "1주외국인매매합(억)",
            "inst_net":        "1주기관매매합(억)",
            "avg_return_1w":   "1주등락률평균(%)",
        })
        return df

    sec_kospi  = db_sector_to_df("KOSPI")
    sec_kosdaq = db_sector_to_df("KOSDAQ")

    # ── 5. market_info (DB에서 조회) ──
    def build_index_dict(market: str):
        mkt_df = db.get_market_history(market, n_weeks=2)
        row = mkt_df[mkt_df["week_date"] == week_date]
        if row.empty:
            return {}
        r = row.iloc[0]
        d = {}
        for src, dst in [
            ("index_close",      "종가"),
            ("daily_pt_change",  "전일대비"),
            ("daily_pct_change", "등락률"),
            ("weekly_pt_change", "주간등락(p)"),
            ("weekly_pct_change", "주간등락률(%)"),
        ]:
            val = r.get(src)
            if val is not None and pd.notna(val):
                d[dst] = val
        return d

    market_info = {
        "KOSPI_index":  build_index_dict("KOSPI"),
        "KOSDAQ_index": build_index_dict("KOSDAQ"),
    }

    return {
        "base_date":          week_date,
        "kospi":              df_kospi,
        "kosdaq":             df_kosdaq,
        "kospi_cap_weight":   cap_kospi,
        "kosdaq_cap_weight":  cap_kosdaq,
        "kospi_sector":       sec_kospi,
        "kosdaq_sector":      sec_kosdaq,
        "kospi_top_stocks":   top_kospi,
        "kosdaq_top_stocks":  top_kosdaq,
        "market_info":        market_info,
        "investor_ranks":     {},
        "n_weeks_in_db":      db.get_week_count(),
        "n_stock_weeks_in_db": db.get_stock_week_count(),
    }


def main():
    config = load_config()

    from modules import database as db_module
    db = db_module.Database(
        config.get("output", {}).get("history_db", "./history.db")
    )

    # 날짜 인자
    if len(sys.argv) > 1:
        week_date = sys.argv[1]
    else:
        week_date = db.get_latest_week_date()

    if not week_date:
        logger.error("DB에 데이터 없음")
        return

    logger.info(f"보고서 재생성 대상 날짜: {week_date}")

    # Excel 경로 확인
    prefix    = config.get("output", {}).get("excel_prefix", "주가자금동향")
    excel_dir = config.get("output", {}).get("excel_dir", "./data")
    xlsx_path = f"{excel_dir}/{prefix}_{week_date}.xlsx"
    if not Path(xlsx_path).exists():
        logger.error(f"Excel 파일 없음: {xlsx_path}")
        return

    # processed 재구성
    processed = reconstruct_processed(xlsx_path, week_date, db)

    # 차트 재생성 (visualizer 코드 변경 반영용)
    from modules import visualizer
    logger.info("차트 PNG 재생성...")
    chart_paths = visualizer.generate_charts(processed, db, config)
    for k, v in chart_paths.items():
        logger.info(f"  → {v}")

    # rotation_data (이력 부족 → 빈 dict)
    rotation_data = {}

    # 보고서 생성 (3회 분할 API 호출)
    from modules import reporter
    report_path = reporter.generate_report(processed, chart_paths, rotation_data, config)
    logger.info(f"✅ 보고서 생성 완료: {report_path}")


if __name__ == "__main__":
    main()
