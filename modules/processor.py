"""
processor.py - 데이터 가공 모듈
파생 컬럼 계산 + 시가 구간별 비중 + 섹터별 집계
"""

import logging
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────
# 파생 컬럼 추가
# ─────────────────────────────────────────

def add_derived_columns(df: pd.DataFrame) -> pd.DataFrame:
    """파생 컬럼 추가 (시가대비 매매비중, 영업이익률 등)"""
    df = df.copy()

    # 컬럼명 표준화: 네이버금융 컬럼 → 내부 표준명
    col_map = {
        "시가총액(억)": "시가총액",
        "기관순매수금액": "1주기관매매",
        "외국인순매수금액": "1주외국인매매",
        "등락률_당일": "1주등락률",  # 주간등락률 없을 때 당일 기준으로 대체
        "ROE": "연간_ROE",           # 네이버금융 ROE → 연간 ROE 매핑
    }
    for src, dst in col_map.items():
        if src in df.columns and dst not in df.columns:
            df[dst] = df[src]

    # 시가총액 대비 기관 매매 비중 (1주)
    if "1주기관매매" in df.columns and "시가총액" in df.columns:
        df["시가대비_기관매매비중_1주"] = df.apply(
            lambda r: r["1주기관매매"] / r["시가총액"] * 100
            if pd.notna(r.get("시가총액")) and r.get("시가총액", 0) != 0 else None,
            axis=1,
        )

    # 시가총액 대비 외국인 매매 비중 (1주)
    if "1주외국인매매" in df.columns and "시가총액" in df.columns:
        df["시가대비_외국인매매비중_1주"] = df.apply(
            lambda r: r["1주외국인매매"] / r["시가총액"] * 100
            if pd.notna(r.get("시가총액")) and r.get("시가총액", 0) != 0 else None,
            axis=1,
        )

    # 영업이익률
    if "영업이익률" not in df.columns:
        if "연간_매출액" in df.columns and "연간_영업이익" in df.columns:
            df["영업이익률"] = df.apply(
                lambda r: r["연간_영업이익"] / r["연간_매출액"] * 100
                if pd.notna(r.get("연간_매출액")) and r.get("연간_매출액", 0) != 0 else None,
                axis=1,
            )

    return df


# ─────────────────────────────────────────
# 시가 구간별 비중 계산
# ─────────────────────────────────────────

KOSPI_CAP_GROUPS = [
    ("Top 10",    lambda rank: rank <= 10),
    ("11~20위",   lambda rank: 11 <= rank <= 20),
    ("21~30위",   lambda rank: 21 <= rank <= 30),
    ("31~50위",   lambda rank: 31 <= rank <= 50),
    ("51~100위",  lambda rank: 51 <= rank <= 100),
    ("101~150위", lambda rank: 101 <= rank <= 150),
    ("151~200위", lambda rank: 151 <= rank <= 200),
    ("1~200위",   lambda rank: rank <= 200),
]

KOSDAQ_CAP_GROUPS = [
    ("Top 10",    lambda rank: rank <= 10),
    ("11~20위",   lambda rank: 11 <= rank <= 20),
    ("21~30위",   lambda rank: 21 <= rank <= 30),
    ("31~50위",   lambda rank: 31 <= rank <= 50),
    ("51~100위",  lambda rank: 51 <= rank <= 100),
    ("101~150위", lambda rank: 101 <= rank <= 150),
    ("1~150위",   lambda rank: rank <= 150),
]


def compute_cap_weight_groups(df: pd.DataFrame, market: str) -> pd.DataFrame:
    """
    시가 구간별 비중 계산
    Returns:
        DataFrame with columns: [구간, 종목수, 구간시총(억), 전체시총(억), 비중(%)]
    """
    if df.empty or "시가총액" not in df.columns:
        return pd.DataFrame()

    df = df.copy()
    # 순위 컬럼 확인
    if "순위" not in df.columns:
        df = df.sort_values("시가총액", ascending=False).reset_index(drop=True)
        df["순위"] = range(1, len(df) + 1)

    total_cap = df["시가총액"].sum()
    n = len(df)

    groups = KOSPI_CAP_GROUPS if market == "KOSPI" else KOSDAQ_CAP_GROUPS
    rows = []

    for group_name, cond in groups:
        mask = df["순위"].apply(cond)
        subset = df[mask]

        if subset.empty:
            continue

        group_cap = subset["시가총액"].sum()
        rows.append({
            "구간": group_name,
            "종목수": len(subset),
            "구간시총(억)": round(group_cap, 2),
            "전체시총(억)": round(total_cap, 2),
            "비중(%)": round(group_cap / total_cap * 100, 4) if total_cap > 0 else 0,
        })

    return pd.DataFrame(rows)


# ─────────────────────────────────────────
# 섹터별 집계
# ─────────────────────────────────────────

def aggregate_by_sector(df: pd.DataFrame, market: str) -> pd.DataFrame:
    """섹터별 주간 매매동향 집계"""
    if df.empty:
        return pd.DataFrame()

    sector_col = "섹터"
    if sector_col not in df.columns:
        return pd.DataFrame()

    df = df[df["시장"] == market].copy() if "시장" in df.columns else df.copy()

    agg_dict = {
        "종목수": ("티커", "count"),
        "시가총액합(억)": ("시가총액", "sum"),
    }

    optional_cols = {
        "1주외국인매매합(억)": "1주외국인매매",
        "1주기관매매합(억)": "1주기관매매",
        "1주등락률평균(%)": "1주등락률",
    }
    for out_col, src_col in optional_cols.items():
        if src_col in df.columns:
            func = "mean" if "등락률" in src_col else "sum"
            agg_dict[out_col] = (src_col, func)

    result = df.groupby(sector_col).agg(**agg_dict).reset_index()
    result = result.rename(columns={sector_col: "섹터"})

    # 섹터 시총 대비 매매 비중
    if "시가총액합(억)" in result.columns:
        if "1주외국인매매합(억)" in result.columns:
            result["외국인_시총대비비중(%)"] = (
                result["1주외국인매매합(억)"] / result["시가총액합(억)"] * 100
            )
        if "1주기관매매합(억)" in result.columns:
            result["기관_시총대비비중(%)"] = (
                result["1주기관매매합(억)"] / result["시가총액합(억)"] * 100
            )

    # 합산 기준 정렬 (외국인 + 기관 순매수 합)
    if "1주외국인매매합(억)" in result.columns and "1주기관매매합(억)" in result.columns:
        result["합산매매(억)"] = result["1주외국인매매합(억)"] + result["1주기관매매합(억)"]
        result = result.sort_values("합산매매(억)", ascending=False)
    elif "시가총액합(억)" in result.columns:
        result = result.sort_values("시가총액합(억)", ascending=False)

    return result.reset_index(drop=True)


# ─────────────────────────────────────────
# 섹터 로테이션 감지 (DB 이력 기반)
# ─────────────────────────────────────────

def detect_rotation(sector_history: pd.DataFrame, n_weeks: int = 4) -> pd.DataFrame:
    """
    섹터 로테이션 분석
    - 3주+ 연속 외국인 순매수 → "모멘텀 지속"
    - 순매수 + 낮은 등락률 → "잠재 상승 후보"
    - 순매수→순매도 전환 → "차익실현 주의"
    """
    if sector_history.empty:
        return pd.DataFrame()

    results = []
    sectors = sector_history["sector"].unique()

    for sector in sectors:
        s_df = sector_history[sector_history["sector"] == sector].sort_values("week_date")
        if len(s_df) < 2:
            continue

        recent = s_df.tail(n_weeks)
        signals = []

        # 연속 순매수 체크
        fore_net_vals = recent["foreign_net"].dropna().tolist()
        if len(fore_net_vals) >= 3:
            consecutive_buy = sum(1 for v in fore_net_vals[-3:] if v > 0)
            if consecutive_buy == 3:
                signals.append("모멘텀 지속(외국인 3주+ 연속 순매수)")

        # 순매수 + 낮은 등락률 체크
        if len(recent) >= 1:
            last = recent.iloc[-1]
            if (pd.notna(last.get("foreign_net")) and last["foreign_net"] > 0 and
                    pd.notna(last.get("avg_return_1w")) and last["avg_return_1w"] < 1.0):
                signals.append("잠재 상승 후보(순매수+낮은등락률)")

        # 순매수→순매도 전환
        if len(fore_net_vals) >= 2:
            if fore_net_vals[-2] > 0 and fore_net_vals[-1] < 0:
                signals.append("차익실현 주의(순매수→순매도 전환)")

        if signals:
            results.append({
                "섹터": sector,
                "신호": " / ".join(signals),
                "최근외국인매매(억)": fore_net_vals[-1] if fore_net_vals else None,
                "최근평균등락률(%)": recent["avg_return_1w"].mean() if "avg_return_1w" in recent.columns else None,
            })

    return pd.DataFrame(results)


# ─────────────────────────────────────────
# 보고서용 주요 종목 추출
# ─────────────────────────────────────────

def extract_top_stocks(df: pd.DataFrame, n: int = 20) -> dict:
    """
    보고서용 상위 종목 추출
    A: 기관매매 금액 순위
    B: 시가대비 기관매매 비중 순위
    C: 외국인매매 금액 순위
    D: 시가대비 외국인매매 비중 순위
    E: 주가 등락률 순위
    """
    result = {}
    base_cols = ["순위", "티커", "종목명", "시가총액"]
    available_cols = [c for c in base_cols if c in df.columns]

    def safe_topn(sort_col: str, extra_cols: list, label: str):
        if sort_col not in df.columns:
            result[label] = pd.DataFrame()
            return
        cols = available_cols + [sort_col] + [c for c in extra_cols if c in df.columns and c not in available_cols]
        cols = list(dict.fromkeys(cols))  # 중복 제거
        sub = df[cols].dropna(subset=[sort_col])
        result[label] = sub.nlargest(n, sort_col).reset_index(drop=True)

    safe_topn("1주기관매매", ["1주기관매매"], "A_기관매매금액")
    safe_topn("시가대비_기관매매비중_1주", ["시가대비_기관매매비중_1주", "1주기관매매"], "B_기관매매비중")
    safe_topn("1주외국인매매", ["1주외국인매매"], "C_외국인매매금액")
    safe_topn("시가대비_외국인매매비중_1주", ["시가대비_외국인매매비중_1주", "1주외국인매매"], "D_외국인매매비중")
    safe_topn("1주등락률", ["1주등락률"], "E_주가등락률")

    return result


# ─────────────────────────────────────────
# 메인 처리 함수
# ─────────────────────────────────────────

def process(raw: dict) -> dict:
    """
    수집 데이터 전처리 및 집계
    Returns: processed dict
    """
    processed = {}

    # investor_ranks (KIS API 기관/외국인 상위) 병합 준비
    investor_ranks = raw.get("investor_ranks", {})

    for market in ["kospi", "kosdaq"]:
        df = raw.get(market, pd.DataFrame())
        if df.empty:
            logger.warning(f"[{market.upper()}] 원본 데이터 없음")
            processed[market] = pd.DataFrame()
            processed[f"{market}_cap_weight"] = pd.DataFrame()
            processed[f"{market}_sector"] = pd.DataFrame()
            processed[f"{market}_top_stocks"] = {}
            continue

        # 순위 컬럼 표준화
        if "순위" not in df.columns:
            df = df.reset_index(drop=True)
            df.insert(0, "순위", range(1, len(df) + 1))

        # 시가총액 컬럼 표준화
        if "시가총액(억)" in df.columns and "시가총액" not in df.columns:
            df = df.rename(columns={"시가총액(억)": "시가총액"})

        # KIS 기관/외국인 순매수 데이터 병합
        inv_df = investor_ranks.get(market.upper(), pd.DataFrame())
        if not inv_df.empty and "티커" in inv_df.columns:
            merge_cols = ["티커"]
            for col in ["기관순매수금액", "외국인순매수금액"]:
                if col in inv_df.columns:
                    merge_cols.append(col)
            df = df.merge(inv_df[merge_cols], on="티커", how="left")
            # 컬럼명 표준화
            if "기관순매수금액" in df.columns:
                df = df.rename(columns={"기관순매수금액": "1주기관매매"})
            if "외국인순매수금액" in df.columns:
                df = df.rename(columns={"외국인순매수금액": "1주외국인매매"})

        # 파생 컬럼 추가
        df = add_derived_columns(df)
        processed[market] = df

        # 시가 구간별 비중
        cap_weight = compute_cap_weight_groups(df, market.upper())
        processed[f"{market}_cap_weight"] = cap_weight
        logger.info(f"[{market.upper()}] 시가 구간 집계: {len(cap_weight)}개 구간")

        # 섹터별 집계
        sector = aggregate_by_sector(df, market.upper())
        processed[f"{market}_sector"] = sector
        logger.info(f"[{market.upper()}] 섹터 집계: {len(sector)}개 섹터")

        # 보고서용 상위 종목
        top = extract_top_stocks(df)
        processed[f"{market}_top_stocks"] = top

    # 지수/시장 정보 전달
    processed["market_info"] = raw.get("market_info", {})
    processed["investor_ranks"] = investor_ranks
    processed["base_date"] = raw.get("base_date", "")
    processed["week_date"] = raw.get("week_date", raw.get("base_date", ""))
    processed["week_start"] = raw.get("week_start", "")
    processed["biz_days"] = raw.get("biz_days", 5)
    processed["is_midweek"] = raw.get("is_midweek", False)
    processed["period_label"] = raw.get("period_label", "")

    return processed
