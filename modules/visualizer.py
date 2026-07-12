"""
visualizer.py - 시계열 차트 생성 모듈 (matplotlib)
6종 PNG 생성:
  cap_weight_kospi / cap_weight_kosdaq  - 시가 비중 추이 (52주)
  flow_kospi / flow_kosdaq              - 투자자 자금 추이 (52주)
  sector_flow_kospi / sector_flow_kosdaq - 섹터별 자금 (이번 주)
"""

import logging
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import pandas as pd

matplotlib.use("Agg")  # GUI 없는 환경

# Mac 한글 폰트
plt.rcParams["font.family"] = "AppleGothic"
plt.rcParams["axes.unicode_minus"] = False

logger = logging.getLogger(__name__)

FIG_DPI = 150
FIG_SIZE_LINE = (14, 6)
FIG_SIZE_BAR = (10, 8)

COLOR_FOREIGN = "#1f77b4"   # 파랑 - 외국인
COLOR_INST = "#d62728"      # 빨강 - 기관
COLOR_INDIV = "#2ca02c"     # 초록 - 개인

# 시가 구간 색상 맵
CAP_COLORS = {
    "Top 10":    "#1f4e79",
    "Top 50":    "#2e75b6",
    "Top 100":   "#9dc3e6",
    "Top 150":   "#bdd7ee",
    "Top 200":   "#deeaf1",
    "11~20위":   "#2e75b6",
    "21~30위":   "#4bacc6",
    "31~50위":   "#9dc3e6",
    "51~100위":  "#c5dff5",
}


# ─────────────────────────────────────────
# 시가 비중 추이 차트
# ─────────────────────────────────────────

def _plot_cap_weight(cap_history: pd.DataFrame, market: str, output_path: Path):
    """시가 구간별 비중 추이 라인 차트"""
    fig, ax = plt.subplots(figsize=FIG_SIZE_LINE, dpi=FIG_DPI)

    if cap_history.empty:
        ax.text(0.5, 0.5, "데이터 없음", ha="center", va="center", transform=ax.transAxes)
        fig.savefig(output_path, bbox_inches="tight")
        plt.close(fig)
        return

    # 피벗: X=week_date, 컬럼=group_name, 값=weight_pct
    pivot = cap_history.pivot_table(index="week_date", columns="group_name", values="weight_pct")

    # 표시할 구간 선택
    if market == "KOSPI":
        show_groups = ["Top 10", "Top 50", "Top 100", "Top 200"]
    else:
        show_groups = ["Top 10", "Top 50", "Top 100", "Top 150"]

    # 누적 합산으로 "Top N" 시리즈 생성
    group_map = {
        "Top 10":  ["Top 10"],
        "Top 50":  ["Top 10", "11~20위", "21~30위", "31~50위"],
        "Top 100": ["Top 10", "11~20위", "21~30위", "31~50위", "51~100위"],
        "Top 150": ["Top 10", "11~20위", "21~30위", "31~50위", "51~100위", "101~150위"],
        "Top 200": ["Top 10", "11~20위", "21~30위", "31~50위", "51~100위", "101~150위", "151~200위"],
    }

    x_labels = list(pivot.index)
    x_pos = range(len(x_labels))

    for grp in show_groups:
        sub_groups = group_map.get(grp, [grp])
        vals = []
        for date in x_labels:
            total = 0
            for sg in sub_groups:
                if sg in pivot.columns and pd.notna(pivot.at[date, sg]):
                    total += pivot.at[date, sg]
            vals.append(total if total > 0 else None)
        color = CAP_COLORS.get(grp, None)
        ax.plot(x_pos, vals, marker="o", markersize=4, label=grp, color=color, linewidth=1.5)

    # X축 레이블 (간격 조정)
    step = max(1, len(x_labels) // 10)
    ax.set_xticks(list(x_pos)[::step])
    ax.set_xticklabels(x_labels[::step], rotation=45, ha="right", fontsize=8)

    ax.set_title(f"{market} 시가총액 구간별 비중 추이", fontsize=14, fontweight="bold")
    ax.set_ylabel("비중 (%)")
    ax.set_xlabel("기준일(주)")
    ax.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.1f%%"))
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(axis="y", alpha=0.3)

    fig.tight_layout()
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)
    logger.info(f"차트 저장: {output_path}")


# ─────────────────────────────────────────
# 투자자 자금 추이 차트
# ─────────────────────────────────────────

def _plot_investor_flow(market_history: pd.DataFrame, stock_flow_history: pd.DataFrame,
                        market: str, output_path: Path):
    """투자자별 주간 자금 추이 Bar 차트.

    데이터 소스 우선순위:
      1) weekly_market.weekly_foreign_net/inst_net (KIS API 기반, 현재 빈 응답으로 None)
      2) weekly_stock의 종목별 매매 주별 합산 (Top 200 기준 fallback)
    개인(individual)은 시장 전체 데이터가 없어 표시 생략.
    """
    import numpy as np

    fig, ax = plt.subplots(figsize=FIG_SIZE_LINE, dpi=FIG_DPI)

    # 데이터 소스 결정 (weekly_market이 비어있으면 stock_flow_history 사용)
    use_stock_fallback = False
    if market_history.empty:
        df = stock_flow_history
        use_stock_fallback = True
    else:
        # weekly_market의 외국인/기관 값이 모두 NULL이면 stock fallback 머지
        mh = market_history.copy()
        fore_all_null = (
            "weekly_foreign_net" not in mh.columns
            or pd.to_numeric(mh.get("weekly_foreign_net"), errors="coerce").isna().all()
        )
        inst_all_null = (
            "weekly_inst_net" not in mh.columns
            or pd.to_numeric(mh.get("weekly_inst_net"), errors="coerce").isna().all()
        )
        if fore_all_null and inst_all_null and not stock_flow_history.empty:
            # weekly_market에 stock 합산을 머지
            df = mh.drop(columns=[c for c in ["weekly_foreign_net", "weekly_inst_net"]
                                   if c in mh.columns], errors="ignore")
            df = df.merge(stock_flow_history, on="week_date", how="left")
            use_stock_fallback = True
        else:
            df = mh

    if df.empty:
        ax.text(0.5, 0.5, "데이터 없음 (2주차 이후 추이 표시됨)",
                ha="center", va="center", transform=ax.transAxes, fontsize=11)
        fig.savefig(output_path, bbox_inches="tight")
        plt.close(fig)
        return

    x_labels = list(df["week_date"])
    x_pos = range(len(x_labels))
    width = 0.38
    x_arr = np.array(list(x_pos))

    def _col_vals(d: pd.DataFrame, col: str) -> list:
        if col in d.columns:
            return pd.to_numeric(d[col], errors="coerce").fillna(0).tolist()
        return [0] * len(x_labels)

    fore = _col_vals(df, "weekly_foreign_net")
    inst = _col_vals(df, "weekly_inst_net")

    ax.bar(x_arr - width / 2, fore, width, label="외국인", color=COLOR_FOREIGN, alpha=0.85)
    ax.bar(x_arr + width / 2, inst, width, label="기관", color=COLOR_INST, alpha=0.85)

    step = max(1, len(x_labels) // 10)
    ax.set_xticks(x_arr[::step])
    ax.set_xticklabels(x_labels[::step], rotation=45, ha="right", fontsize=8)

    ax.axhline(0, color="black", linewidth=0.8)
    title_suffix = " (Top 200 종목 합산)" if use_stock_fallback else ""
    ax.set_title(f"{market} 주간 외국인·기관 자금 추이{title_suffix}",
                 fontsize=14, fontweight="bold")
    ax.set_ylabel("순매수 (억원)")
    ax.set_xlabel("기준일(주)")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:,.0f}"))
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(axis="y", alpha=0.3)

    fig.tight_layout()
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)
    logger.info(f"차트 저장: {output_path}")


# ─────────────────────────────────────────
# 섹터별 자금 차트 (이번 주)
# ─────────────────────────────────────────

def _plot_sector_flow(sector_df: pd.DataFrame, market: str, output_path: Path):
    """섹터별 외국인/기관 grouped horizontal bar 차트 (좌우 분리)"""
    fig, ax = plt.subplots(figsize=(11, 9), dpi=FIG_DPI)

    if sector_df.empty:
        ax.text(0.5, 0.5, "데이터 없음", ha="center", va="center", transform=ax.transAxes)
        fig.savefig(output_path, bbox_inches="tight")
        plt.close(fig)
        return

    fore_col = "1주외국인매매합(억)"
    inst_col = "1주기관매매합(억)"

    # 절댓값 합산 기준 상위 N개 (음수 매도 섹터도 포함)
    sector_df = sector_df.copy()
    if fore_col in sector_df.columns and inst_col in sector_df.columns:
        sector_df["_abs_sum"] = (
            sector_df[fore_col].fillna(0).abs() + sector_df[inst_col].fillna(0).abs()
        )
        sector_df = sector_df.nlargest(20, "_abs_sum")
        # 외국인+기관 단순 합 기준 오름차순 정렬(작은값이 아래쪽 → 위로 갈수록 큰 매수)
        sector_df["_signed_sum"] = (
            sector_df[fore_col].fillna(0) + sector_df[inst_col].fillna(0)
        )
        sector_df = sector_df.sort_values("_signed_sum", ascending=True)
    elif fore_col in sector_df.columns:
        sector_df = sector_df.nlargest(20, fore_col).sort_values(fore_col)
    else:
        sector_df = sector_df.head(20)

    labels = sector_df["섹터"].tolist()
    fore_vals = sector_df[fore_col].fillna(0).tolist() if fore_col in sector_df.columns else [0] * len(labels)
    inst_vals = sector_df[inst_col].fillna(0).tolist() if inst_col in sector_df.columns else [0] * len(labels)

    import numpy as np
    y_pos = np.arange(len(labels))
    bar_h = 0.4  # 각 막대 두께

    ax.barh(y_pos + bar_h / 2, fore_vals, height=bar_h,
            color=COLOR_FOREIGN, alpha=0.85, label="외국인")
    ax.barh(y_pos - bar_h / 2, inst_vals, height=bar_h,
            color=COLOR_INST, alpha=0.85, label="기관")

    ax.set_yticks(y_pos)
    ax.set_yticklabels(labels, fontsize=9)
    ax.axvline(0, color="black", linewidth=0.8)
    ax.set_title(f"{market} 섹터별 주간 자금 동향 (외국인·기관 분리)",
                 fontsize=13, fontweight="bold")
    ax.set_xlabel("순매수 (억원)")
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:,.0f}"))
    ax.legend(loc="lower right", fontsize=10)
    ax.grid(axis="x", alpha=0.3)

    fig.tight_layout()
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)
    logger.info(f"차트 저장: {output_path}")


# ─────────────────────────────────────────
# 메인 차트 생성 함수
# ─────────────────────────────────────────

def generate_charts(processed: dict, db, config: dict) -> dict:
    """
    6종 차트 PNG 생성
    Returns: {차트명: 파일경로} dict
    """
    base_date = processed.get("base_date", "")
    chart_dir = Path(config.get("output", {}).get("chart_dir", "./data/charts"))
    chart_dir.mkdir(parents=True, exist_ok=True)

    paths = {}

    for market in ["KOSPI", "KOSDAQ"]:
        mkt_lower = market.lower()

        # 1. 시가 비중 추이 차트
        cap_history = db.get_cap_weight_history(market, n_weeks=52)
        cap_path = chart_dir / f"cap_weight_{mkt_lower}_{base_date}.png"
        _plot_cap_weight(cap_history, market, cap_path)
        paths[f"cap_weight_{mkt_lower}"] = str(cap_path)

        # 2. 투자자 자금 추이 차트 (weekly_market NULL fallback: weekly_stock 합산)
        market_history = db.get_market_history(market, n_weeks=52)
        stock_flow_history = db.get_market_flow_from_stock(market, n_weeks=52)
        flow_path = chart_dir / f"flow_{mkt_lower}_{base_date}.png"
        _plot_investor_flow(market_history, stock_flow_history, market, flow_path)
        paths[f"flow_{mkt_lower}"] = str(flow_path)

        # 3. 섹터별 자금 차트 (이번 주)
        sector_df = processed.get(f"{mkt_lower}_sector", pd.DataFrame())
        sector_path = chart_dir / f"sector_flow_{mkt_lower}_{base_date}.png"
        _plot_sector_flow(sector_df, market, sector_path)
        paths[f"sector_flow_{mkt_lower}"] = str(sector_path)

    return paths
