"""ETF 수급 섹션 — 결정적 markdown 렌더(LLM 미사용).

공유용·개인용 공통 본문에 삽입된다(reporter가 호출). 데이터는 crawler.crawl_krx_etf_flows 산출.
"""
import logging

import pandas as pd

logger = logging.getLogger(__name__)

# (제목, 정렬 컬럼, 오름차순 여부, [표시 컬럼 순서])
_DIRECTIONS = [
    ("외국인 순매수 상위", "1주외국인매매", False, ["1주외국인매매", "1주기관매매"]),
    ("외국인 순매도 상위", "1주외국인매매", True, ["1주외국인매매", "1주기관매매"]),
    ("기관 순매수 상위", "1주기관매매", False, ["1주기관매매", "1주외국인매매"]),
    ("기관 순매도 상위", "1주기관매매", True, ["1주기관매매", "1주외국인매매"]),
]
_LABEL = {"1주외국인매매": "외국인", "1주기관매매": "기관"}


def _fmt(v) -> str:
    if v is None or pd.isna(v):
        return "-"
    return f"{v:+,.1f}"


def _direction_table(flows: pd.DataFrame, sort_col: str, ascending: bool, show_cols: list, n: int) -> list:
    sub = flows.dropna(subset=[sort_col]).sort_values(sort_col, ascending=ascending).head(n)
    header = "| ETF | " + " | ".join(f"{_LABEL[c]}(억)" for c in show_cols) + " |"
    sep = "|---|" + "|".join(["---"] * len(show_cols)) + "|"
    lines = [header, sep]
    for _, r in sub.iterrows():
        cells = " | ".join(_fmt(r.get(c)) for c in show_cols)
        lines.append(f"| {r.get('종목명', r.get('티커'))} | {cells} |")
    return lines


def build_etf_section(etf_flows, etf_market_agg, n: int = 5) -> str:
    """ETF 수급 섹션 markdown 생성. 빈 flows → "" (호출자가 섹션 생략)."""
    if etf_flows is None or not isinstance(etf_flows, pd.DataFrame) or etf_flows.empty:
        return ""
    lines = [
        "## ETF 수급",
        "",
        "> 개별 ETF 기관·외국인 순매수(KRX 거래대금 기준, 억원). 순매수·순매도 상위 종목 기준.",
        "",
    ]
    if etf_market_agg:
        parts = [f"{k} {etf_market_agg[k]:+,.0f}억" for k in ["외국인", "기관", "개인"] if k in etf_market_agg]
        if parts:
            lines += ["**ETF 시장 전체**: " + " · ".join(parts), ""]

    for title, sort_col, ascending, show_cols in _DIRECTIONS:
        lines.append(f"**{title}**")
        lines += _direction_table(etf_flows, sort_col, ascending, show_cols, n)
        lines.append("")

    # 최상위 무브머 카피 (결정적)
    fore_top = etf_flows.dropna(subset=["1주외국인매매"]).sort_values("1주외국인매매", ascending=False)
    inst_top = etf_flows.dropna(subset=["1주기관매매"]).sort_values("1주기관매매", ascending=False)
    if not fore_top.empty and not inst_top.empty:
        f, i = fore_top.iloc[0], inst_top.iloc[0]
        lines.append(
            f"> 최상위: 외국인은 {f.get('종목명', f['티커'])}({_fmt(f['1주외국인매매'])}억), "
            f"기관은 {i.get('종목명', i['티커'])}({_fmt(i['1주기관매매'])}억)에 집중."
        )
    return "\n".join(lines) + "\n"
