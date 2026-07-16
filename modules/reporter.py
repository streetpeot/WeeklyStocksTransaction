"""
reporter.py - AI 보고서 자동 생성 모듈 (claude-sonnet-4-6)
Markdown 파일로 주간 자금동향 보고서 저장 (3회 분할 API 호출)

섹션 분할:
  파트1 (API 호출 1): ## 1. 이번 주 시장 요약  +  ## 2. 외국인 자금 동향
  파트2 (API 호출 2): ## 3. 기관 자금 동향    +  ## 4. 섹터 로테이션 분석
  파트3 (API 호출 3): ## 5. 시가총액 집중도   +  ## 6. 개인 투자자 동향  +  ## 7. 투자 시사점
"""

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────
# 포맷 헬퍼
# ─────────────────────────────────────────

def _fmt_num(v, unit="억", decimals=0) -> str:
    if v is None or pd.isna(v):
        return "N/A"
    try:
        if decimals == 0:
            return f"{v:,.0f}{unit}"
        else:
            return f"{v:,.{decimals}f}{unit}"
    except (TypeError, ValueError):
        return "N/A"


def _fmt_pct(v) -> str:
    if v is None or pd.isna(v):
        return "N/A"
    try:
        sign = "+" if v > 0 else ""
        return f"{sign}{v:.2f}%"
    except (TypeError, ValueError):
        return "N/A"


def _df_to_md_table(df: pd.DataFrame, max_rows: int = 20) -> str:
    """DataFrame → Markdown 테이블 문자열"""
    if df is None or df.empty:
        return "데이터 없음\n"
    df = df.head(max_rows)
    cols = list(df.columns)
    header = "| " + " | ".join(str(c) for c in cols) + " |"
    divider = "| " + " | ".join("---" for _ in cols) + " |"
    rows = []
    for _, row in df.iterrows():
        cells = []
        for c in cols:
            v = row[c]
            if isinstance(v, float):
                cells.append(f"{v:,.2f}" if not pd.isna(v) else "N/A")
            elif v is None or (isinstance(v, float) and pd.isna(v)):
                cells.append("N/A")
            else:
                cells.append(str(v))
        rows.append("| " + " | ".join(cells) + " |")
    return "\n".join([header, divider] + rows) + "\n"


# ─────────────────────────────────────────
# 공유 데이터 컨텍스트 구성
# ─────────────────────────────────────────

def _period_top_table(df: pd.DataFrame, sort_col: str, market: str, label: str,
                       n: int = 20) -> str:
    """다기간 누적 매매(2주/1개월/3개월) 기준 상위 N개 종목 Markdown 테이블"""
    if df is None or df.empty or sort_col not in df.columns:
        return f"**{label}**: 미제공 (해당 기간 누적 데이터 없음)\n"
    cols = [c for c in ["순위", "티커", "종목명", "시가총액", sort_col, "섹터", "1주등락률"]
            if c in df.columns]
    sub = df[cols].dropna(subset=[sort_col])
    if sub.empty:
        return f"**{label}**: 미제공 (해당 기간 누적 데이터 없음)\n"
    sub = sub.nlargest(n, sort_col).reset_index(drop=True)
    return f"**{label}**\n" + _df_to_md_table(sub)


def _build_data_context(processed: dict, chart_paths: dict, rotation_data: dict) -> dict:
    """보고서 작성에 필요한 모든 데이터 문자열을 미리 구성하여 dict로 반환"""
    base_date = processed.get("base_date", "")
    date_str = f"{base_date[:4]}년 {base_date[4:6]}월 {base_date[6:8]}일" if len(base_date) == 8 else base_date
    is_midweek = processed.get("is_midweek", False)
    period_label = processed.get("period_label", "")
    biz_days = processed.get("biz_days", 5)
    n_weeks = processed.get("n_weeks_in_db", 1)
    n_stock_weeks = processed.get("n_stock_weeks_in_db", 0)

    # 데이터 가용성
    avail_weekly_change = n_weeks >= 2
    avail_2w = n_stock_weeks >= 2
    avail_1m = n_stock_weeks >= 4
    avail_3m = n_stock_weeks >= 12

    def avail_str(flag: bool, label: str, needed: int, current: int) -> str:
        if flag:
            return f"- {label}: 제공 ✓"
        return f"- {label}: 미제공 (현재 {current}주 누적, {needed}주 필요)"

    data_availability = "\n".join([
        f"**DB 누적 이력: 지수 {n_weeks}주 / 종목별 투자자 {n_stock_weeks}주**",
        avail_str(avail_weekly_change, "주간 지수 등락", 2, n_weeks),
        avail_str(avail_2w, "2주 기관/외국인 매매 누적", 2, n_stock_weeks),
        avail_str(avail_1m, "1개월 기관/외국인 매매 누적", 4, n_stock_weeks),
        avail_str(avail_3m, "3개월 기관/외국인 매매 누적", 12, n_stock_weeks),
    ])

    # 지수 / 투자자 정보
    market_info = processed.get("market_info", {})
    investor_ranks = processed.get("investor_ranks", {})

    def get_index_summary(market: str) -> str:
        idx = market_info.get(f"{market}_index", {})
        inv_df = investor_ranks.get(market, pd.DataFrame())
        lines = [f"**{market}**"]
        if idx:
            close = idx.get("종가")
            daily_pt = idx.get("전일대비")
            daily_pct = idx.get("등락률")
            weekly_pt = idx.get("주간등락(p)")
            weekly_pct = idx.get("주간등락률(%)")
            if close:
                lines.append(f"- 종가: {close:,.2f}p")
            if weekly_pt is not None and weekly_pct is not None:
                lines.append(f"- 주간등락: {weekly_pt:+.2f}p ({weekly_pct:+.2f}%)")
            else:
                lines.append(f"- 주간등락: 미산출 (현재 {n_weeks}주 누적, 2주 필요)")
            if daily_pt and daily_pct:
                lines.append(f"- 일간등락: {daily_pt:+.2f}p ({daily_pct:+.2f}%)")
        if not inv_df.empty:
            if "기관순매수금액" in inv_df.columns:
                lines.append(f"- 기관 Top30 순매수 합: {inv_df['기관순매수금액'].sum():+,.0f}억")
            if "외국인순매수금액" in inv_df.columns:
                lines.append(f"- 외국인 Top30 순매수 합: {inv_df['외국인순매수금액'].sum():+,.0f}억")
        return "\n".join(lines)

    market_summary = get_index_summary("KOSPI") + "\n\n" + get_index_summary("KOSDAQ")

    # 시가 구간 비중
    def cap_weight_summary(market: str) -> str:
        cap_df = processed.get(f"{market.lower()}_cap_weight", pd.DataFrame())
        if cap_df is None or cap_df.empty:
            return f"{market} 시총비중 데이터 없음"
        return _df_to_md_table(cap_df)

    # 상위 종목 테이블
    def top_table(market: str, key: str, label: str) -> str:
        tops = processed.get(f"{market.lower()}_top_stocks", {})
        df = tops.get(key, pd.DataFrame())
        if df is None or df.empty:
            return f"**{label}**: 데이터 없음\n"
        display_cols = [c for c in ["순위", "종목명", "시가총액", "1주기관매매",
                                     "1주외국인매매", "시가대비_기관매매비중_1주",
                                     "시가대비_외국인매매비중_1주", "1주등락률"] if c in df.columns]
        return f"**{label}**\n" + _df_to_md_table(df[display_cols])

    # 섹터 집계
    def sector_summary(market: str) -> str:
        sec_df = processed.get(f"{market.lower()}_sector", pd.DataFrame())
        if sec_df is None or sec_df.empty:
            return f"{market} 섹터 데이터 없음"
        disp = [c for c in ["섹터", "종목수", "시가총액합(억)", "1주외국인매매합(억)",
                             "1주기관매매합(억)", "1주등락률평균(%)"] if c in sec_df.columns]
        return _df_to_md_table(sec_df[disp])

    # 다기간 누적 매매 상위 종목 (3개월/1개월 기준)
    def multi_period_tables(market: str) -> dict:
        df = processed.get(market.lower(), pd.DataFrame())
        if df is None or df.empty:
            return {
                "3m_fore": f"**{market} 3개월 외국인 누적 매매 TOP20**: 미제공\n",
                "3m_inst": f"**{market} 3개월 기관 누적 매매 TOP20**: 미제공\n",
                "1m_fore": f"**{market} 1개월 외국인 누적 매매 TOP20**: 미제공\n",
                "1m_inst": f"**{market} 1개월 기관 누적 매매 TOP20**: 미제공\n",
            }
        return {
            "3m_fore": _period_top_table(df, "3개월외국인매매", market,
                                         f"{market} 3개월 외국인 누적 매매 TOP20"),
            "3m_inst": _period_top_table(df, "3개월기관매매", market,
                                         f"{market} 3개월 기관 누적 매매 TOP20"),
            "1m_fore": _period_top_table(df, "1개월외국인매매", market,
                                         f"{market} 1개월 외국인 누적 매매 TOP20"),
            "1m_inst": _period_top_table(df, "1개월기관매매", market,
                                         f"{market} 1개월 기관 누적 매매 TOP20"),
        }
    kospi_periods = multi_period_tables("KOSPI")
    kosdaq_periods = multi_period_tables("KOSDAQ")

    # 섹터 로테이션
    rot_kospi = rotation_data.get("KOSPI", pd.DataFrame())
    rot_kosdaq = rotation_data.get("KOSDAQ", pd.DataFrame())
    rot_text = ""
    if not rot_kospi.empty:
        rot_text += "**KOSPI 섹터 로테이션 신호**\n" + _df_to_md_table(rot_kospi) + "\n"
    if not rot_kosdaq.empty:
        rot_text += "**KOSDAQ 섹터 로테이션 신호**\n" + _df_to_md_table(rot_kosdaq) + "\n"
    if not rot_text:
        rot_text = "뚜렷한 섹터 로테이션 신호 없음 (DB 이력 4주 이상 누적 후 제공)\n"

    # 보고서 제목/기간 표시
    if is_midweek and period_label:
        report_title = f"코스피/코스닥 중간 수급동향 ({period_label})"
        period_desc = f"{period_label} ({biz_days}거래일)"
        midweek_note = (
            f"\n**참고**: 이 보고서는 {period_label} 기간({biz_days}거래일)의 중간 분석입니다. "
            "주 후반 데이터는 포함되지 않았습니다."
        )
    else:
        report_title = f"코스피/코스닥 주간 자금동향 ({date_str} 기준)"
        period_desc = f"{date_str} 기준 (5거래일)"
        midweek_note = ""

    return {
        "base_date": base_date,
        "date_str": date_str,
        "is_midweek": is_midweek,
        "period_label": period_label,
        "report_title": report_title,
        "period_desc": period_desc,
        "midweek_note": midweek_note,
        "n_weeks": n_weeks,
        "data_availability": data_availability,
        "market_summary": market_summary,
        "cap_weight_kospi": cap_weight_summary("KOSPI"),
        "cap_weight_kosdaq": cap_weight_summary("KOSDAQ"),
        "inst_amount_kospi":  top_table("KOSPI",  "A_기관매매금액", "KOSPI 기관매매금액 상위 20"),
        "inst_ratio_kospi":   top_table("KOSPI",  "B_기관매매비중", "KOSPI 시가대비 기관매매비중 상위 20"),
        "inst_amount_kosdaq": top_table("KOSDAQ", "A_기관매매금액", "KOSDAQ 기관매매금액 상위 20"),
        "inst_ratio_kosdaq":  top_table("KOSDAQ", "B_기관매매비중", "KOSDAQ 시가대비 기관매매비중 상위 20"),
        "fore_amount_kospi":  top_table("KOSPI",  "C_외국인매매금액", "KOSPI 외국인매매금액 상위 20"),
        "fore_ratio_kospi":   top_table("KOSPI",  "D_외국인매매비중", "KOSPI 시가대비 외국인매매비중 상위 20"),
        "fore_amount_kosdaq": top_table("KOSDAQ", "C_외국인매매금액", "KOSDAQ 외국인매매금액 상위 20"),
        "fore_ratio_kosdaq":  top_table("KOSDAQ", "D_외국인매매비중", "KOSDAQ 시가대비 외국인매매비중 상위 20"),
        "return_kospi":  top_table("KOSPI",  "E_주가등락률", "KOSPI 주간등락률 상위 20"),
        "return_kosdaq": top_table("KOSDAQ", "E_주가등락률", "KOSDAQ 주간등락률 상위 20"),
        "sector_kospi":  sector_summary("KOSPI"),
        "sector_kosdaq": sector_summary("KOSDAQ"),
        "rotation": rot_text,
        # 다기간 누적 매매 (DB 이력 기반)
        "kospi_3m_fore": kospi_periods["3m_fore"],
        "kospi_3m_inst": kospi_periods["3m_inst"],
        "kospi_1m_fore": kospi_periods["1m_fore"],
        "kospi_1m_inst": kospi_periods["1m_inst"],
        "kosdaq_3m_fore": kosdaq_periods["3m_fore"],
        "kosdaq_3m_inst": kosdaq_periods["3m_inst"],
        "kosdaq_1m_fore": kosdaq_periods["1m_fore"],
        "kosdaq_1m_inst": kosdaq_periods["1m_inst"],
        # 데이터 가용성 flag (프롬프트 조건부 섹션용)
        "avail_3m": avail_3m,
        "avail_1m": avail_1m,
        "avail_2w": avail_2w,
    }


# ─────────────────────────────────────────
# 파트별 프롬프트 구성
# ─────────────────────────────────────────

def _build_prompt_part1(ctx: dict) -> str:
    """파트1: 섹션 1 (이번 주 시장 요약) + 섹션 2 (외국인 자금 동향)
    + 1개월/3개월 누적 외국인 추세 분석 (DB 이력 가용 시)
    """
    report_title = ctx["report_title"]
    period_desc = ctx["period_desc"]
    midweek_note = ctx["midweek_note"]
    avail_3m = ctx.get("avail_3m", False)
    avail_1m = ctx.get("avail_1m", False)

    # 다기간 누적 외국인 매매 섹션 (조건부)
    multi_period_block = ""
    section_25_directive = ""
    if avail_3m or avail_1m:
        multi_period_block = "\n### KOSPI 3개월 외국인 누적 매매 TOP20\n" + ctx["kospi_3m_fore"]
        multi_period_block += "\n### KOSPI 1개월 외국인 누적 매매 TOP20\n" + ctx["kospi_1m_fore"]
        multi_period_block += "\n### KOSDAQ 3개월 외국인 누적 매매 TOP20\n" + ctx["kosdaq_3m_fore"]
        multi_period_block += "\n### KOSDAQ 1개월 외국인 누적 매매 TOP20\n" + ctx["kosdaq_1m_fore"]
        section_25_directive = (
            "\n  ### 2-5. 1개월/3개월 누적 외국인 수급 추세 분석\n"
            "    - 위에 제공된 1개월·3개월 누적 외국인 매매 TOP20 표를 기반으로 \n"
            "      **장기적 자금 유입 종목(누적 상위)** 과 **이번 주 단기 매수 종목** 의 \n"
            "      차이를 비교하고, 외국인의 '지속적 매집 vs 단기 매매' 패턴을 구분해 해석하세요.\n"
            "    - 1개월과 3개월 누적이 동시에 상위인 종목 → 외국인의 구조적 매수 신호\n"
            "    - 이번 주만 상위·3개월 누적은 음수인 종목 → 단기 트레이딩성 매수\n"
            "    - 3개월 누적은 상위인데 이번 주 빠진 종목 → 차익실현 진행 중\n"
        )

    return f"""당신은 한국 주식시장 전문 분석가입니다.
아래 데이터를 바탕으로 {period_desc} 주가 자금동향 보고서의 **파트1 (섹션 1~2)**을 작성하세요.

## 지침
- 개인 투자자를 위한 실용적 인사이트 중심
- 숫자는 반드시 인용하며 해석 제공
- 섹터 로테이션, 외국인 수급 패턴에 주목
- 한국어로 작성, Markdown 형식
- **아래 출력 구조(섹션 1~2)만 생성하세요.** 섹션 3 이후는 작성하지 마세요.
- **차트 이미지 (`![...](...)`) 는 절대 출력에 포함하지 마세요.** 차트는 후처리로 자동 삽입됩니다.
- **데이터 가용성**: '미제공'으로 표시된 항목은 해당 섹션에서 "현재 DB 이력 N주 누적 중 — 데이터 축적 후 제공 예정"이라고 명시하세요
{midweek_note}

---

## 입력 데이터

### 데이터 가용성
{ctx["data_availability"]}

### 증시 지수/투자자별 매매동향
{ctx["market_summary"]}

### KOSPI 외국인 매매금액 TOP20 (이번 주)
{ctx["fore_amount_kospi"]}
### KOSPI 시가대비 외국인매매비중 TOP20 (이번 주)
{ctx["fore_ratio_kospi"]}
### KOSDAQ 외국인 매매금액 TOP20 (이번 주)
{ctx["fore_amount_kosdaq"]}
### KOSDAQ 시가대비 외국인매매비중 TOP20 (이번 주)
{ctx["fore_ratio_kosdaq"]}
### KOSPI 주가 등락률 TOP20
{ctx["return_kospi"]}
### KOSDAQ 주가 등락률 TOP20
{ctx["return_kosdaq"]}
{multi_period_block}

---

## 출력 구조 (이 구조를 그대로 사용하세요)

# {report_title}

## 1. 이번 주 시장 요약

## 2. 외국인 자금 동향
  ### 2-1. 외국인 순매수 규모 (코스피/코스닥)
  ### 2-2. 순매수 금액 상위 20개 종목
  ### 2-3. 시총 대비 비중 순매수 상위 20개 종목 (소형주 발굴)
  ### 2-4. 등락률 상위 20개 종목 분석 (수급-주가 상관관계){section_25_directive}
"""


def _build_prompt_part2(ctx: dict) -> str:
    """파트2: 섹션 3 (기관 자금 동향) + 섹션 4 (섹터 로테이션 분석)
    + 1개월/3개월 누적 기관 추세 분석 (DB 이력 가용 시)
    """
    period_desc = ctx["period_desc"]
    midweek_note = ctx["midweek_note"]
    avail_3m = ctx.get("avail_3m", False)
    avail_1m = ctx.get("avail_1m", False)

    multi_period_block = ""
    section_35_directive = ""
    if avail_3m or avail_1m:
        multi_period_block = "\n### KOSPI 3개월 기관 누적 매매 TOP20\n" + ctx["kospi_3m_inst"]
        multi_period_block += "\n### KOSPI 1개월 기관 누적 매매 TOP20\n" + ctx["kospi_1m_inst"]
        multi_period_block += "\n### KOSDAQ 3개월 기관 누적 매매 TOP20\n" + ctx["kosdaq_3m_inst"]
        multi_period_block += "\n### KOSDAQ 1개월 기관 누적 매매 TOP20\n" + ctx["kosdaq_1m_inst"]
        section_35_directive = (
            "\n  ### 3-5. 1개월/3개월 누적 기관 수급 추세 분석\n"
            "    - 1개월·3개월 누적 기관 매매 TOP20 데이터를 활용해 \n"
            "      **장기적으로 기관이 모으는 종목**과 **이번 주만 단기 매수된 종목**을 비교하세요.\n"
            "    - 3개월 누적 기관 매수 TOP과 이번 주 외국인 매수 TOP의 교집합 종목은 \n"
            "      외국인·기관 합치 시그널로 별도 강조하세요.\n"
        )

    return f"""당신은 한국 주식시장 전문 분석가입니다.
아래 데이터를 바탕으로 {period_desc} 주가 자금동향 보고서의 **파트2 (섹션 3~4)**을 작성하세요.

## 지침
- 개인 투자자를 위한 실용적 인사이트 중심
- 숫자는 반드시 인용하며 해석 제공
- 기관 수급 패턴 및 섹터 로테이션에 주목
- 한국어로 작성, Markdown 형식
- **## 3. 으로 시작하세요.** (# 제목 헤더와 섹션 1~2는 이미 작성됨)
- **섹션 3~4만 생성하세요.** 섹션 5 이후는 작성하지 마세요.
- **차트 이미지 (`![...](...)`) 는 절대 출력에 포함하지 마세요.** 차트는 후처리로 자동 삽입됩니다.
- **데이터 가용성**: '미제공'으로 표시된 항목은 해당 섹션에서 "현재 DB 이력 N주 누적 중 — 데이터 축적 후 제공 예정"이라고 명시하세요
{midweek_note}

---

## 입력 데이터

### 데이터 가용성
{ctx["data_availability"]}

### 증시 지수 요약
{ctx["market_summary"]}

### KOSPI 기관 매매금액 TOP20 (이번 주)
{ctx["inst_amount_kospi"]}
### KOSPI 시가대비 기관매매비중 TOP20 (이번 주)
{ctx["inst_ratio_kospi"]}
### KOSDAQ 기관 매매금액 TOP20 (이번 주)
{ctx["inst_amount_kosdaq"]}
### KOSDAQ 시가대비 기관매매비중 TOP20 (이번 주)
{ctx["inst_ratio_kosdaq"]}
### KOSPI 섹터별 매매동향
{ctx["sector_kospi"]}
### KOSDAQ 섹터별 매매동향
{ctx["sector_kosdaq"]}
### 섹터 로테이션 신호 (최근 4주)
{ctx["rotation"]}
{multi_period_block}

---

## 출력 구조 (이 구조를 그대로 사용하세요)

## 3. 기관 자금 동향
  ### 3-1. 기관 순매수 규모 (코스피/코스닥)
  ### 3-2. 순매수 금액 상위 20개 종목
  ### 3-3. 시총 대비 비중 순매수 상위 20개 종목
  ### 3-4. 등락률 상위 20개 종목 분석{section_35_directive}

## 4. 섹터 로테이션 분석
  ### 4-1. 이번 주 섹터별 외국인·기관 자금 순위
  ### 4-2. 연속 순매수/이탈 섹터 (최근 4주 패턴)
    - N주 연속 순매수 → "모멘텀 지속"
    - 순매수 + 낮은 등락률 → "잠재 상승 후보"
    - 순매수→순매도 전환 → "차익실현 주의"
  ### 4-3. 섹터 로테이션 시사점
"""


def _build_prompt_part3(ctx: dict) -> str:
    """파트3: 섹션 5 (시총 집중도) + 섹션 6 (개인 투자자 동향) + 섹션 7 (투자 시사점)"""
    period_desc = ctx["period_desc"]
    midweek_note = ctx["midweek_note"]
    return f"""당신은 한국 주식시장 전문 분석가입니다.
아래 데이터를 바탕으로 {period_desc} 주가 자금동향 보고서의 **파트3 (섹션 5~7)**을 작성하세요.

## 지침
- 개인 투자자를 위한 실용적 인사이트 중심
- 숫자는 반드시 인용하며 해석 제공
- 한국어로 작성, Markdown 형식
- **## 5. 으로 시작하세요.** (# 제목 헤더와 섹션 1~4는 이미 작성됨)
- **섹션 5~7을 모두 작성하세요.** 이것이 보고서의 마지막 파트입니다.
- 섹션 7 마지막에 반드시 투자 위험 고지를 포함하세요.
- **차트 이미지 (`![...](...)`) 는 절대 출력에 포함하지 마세요.** 차트는 후처리로 자동 삽입됩니다.
- **데이터 가용성**: '미제공'으로 표시된 항목은 해당 섹션에서 "현재 DB 이력 N주 누적 중 — 데이터 축적 후 제공 예정"이라고 명시하세요
{midweek_note}

---

## 입력 데이터

### 데이터 가용성
{ctx["data_availability"]}

### 증시 지수 및 투자자별 매매동향 (개인 수급 분석용)
{ctx["market_summary"]}

### KOSPI 시가총액 구간별 비중
{ctx["cap_weight_kospi"]}
### KOSDAQ 시가총액 구간별 비중
{ctx["cap_weight_kosdaq"]}

---

## 출력 구조 (이 구조를 그대로 사용하세요)

## 5. 시가총액 집중도 분석
  - 코스피/코스닥 구간별 비중 현황
  - 자금 쏠림/분산 여부 해석

## 6. 개인 투자자 동향 및 시장 전체 흐름

## 7. 투자 시사점
  - 주의사항: 본 보고서는 투자 참고 자료이며 투자 손실에 대한 책임은 투자자 본인에게 있습니다.
"""


# ─────────────────────────────────────────
# AI 호출
# ─────────────────────────────────────────

def _call_anthropic(prompt: str, model: str, api_key: str, max_tokens: int = 8192) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=api_key, timeout=300.0, max_retries=2)
    message = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


def _call_openai(prompt: str, model: str, api_key: str, max_tokens: int = 8192) -> str:
    import openai
    client = openai.OpenAI(api_key=api_key)
    resp = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.choices[0].message.content


def _call_google(prompt: str, model: str, api_key: str, max_tokens: int = 8192) -> str:
    import google.generativeai as genai
    genai.configure(api_key=api_key)
    m = genai.GenerativeModel(model)
    resp = m.generate_content(
        prompt,
        generation_config={"max_output_tokens": max_tokens},
    )
    return resp.text


def _call_ai(prompt: str, provider: str, model: str, api_key: str, max_tokens: int) -> str:
    """AI 제공자별 호출 디스패처"""
    if provider == "anthropic":
        return _call_anthropic(prompt, model, api_key, max_tokens)
    elif provider == "openai":
        return _call_openai(prompt, model, api_key, max_tokens)
    elif provider == "google":
        return _call_google(prompt, model, api_key, max_tokens)
    else:
        raise ValueError(f"알 수 없는 AI 제공자: {provider}")


# ─────────────────────────────────────────
# 차트 후처리 삽입
# ─────────────────────────────────────────

def _append_charts(section_text: str, base_date: str, charts: list) -> str:
    """AI가 생성한 섹션 텍스트 뒤에 차트 이미지 마크다운을 결정론적으로 삽입.
    charts: [(alt, filename_stem)] 리스트. filename_stem은 base_date 미포함.
    """
    if not charts:
        return section_text
    lines = ["", ""]
    for alt, stem in charts:
        lines.append(f"![{alt}](charts/{stem}_{base_date}.png)")
    return section_text.rstrip() + "\n".join(lines) + "\n"


# ─────────────────────────────────────────
# ETF 수급 섹션 삽입
# ─────────────────────────────────────────

def _append_etf_section(report_text: str, processed: dict) -> str:
    """ETF 수급 섹션을 본문 뒤에 결정적 삽입(있을 때만). 공유용에 포함 → 개인용 상속."""
    from modules import etf_section
    etf_md = etf_section.build_etf_section(
        processed.get("etf_flows"), processed.get("etf_market_agg"))
    if etf_md:
        return report_text.rstrip() + "\n\n---\n\n" + etf_md
    return report_text


# ─────────────────────────────────────────
# 보고서 생성 (3회 분할 API 호출)
# ─────────────────────────────────────────

def generate_report(
    processed: dict,
    chart_paths: dict,
    rotation_data: dict,
    config: dict,
) -> str:
    """AI 보고서 생성 및 저장 (파트1~3 분할 호출 후 합산)"""
    base_date = processed.get("base_date", "")
    ai_cfg = config.get("ai", {})
    provider = ai_cfg.get("provider", "anthropic").lower()
    model = ai_cfg.get("model", "claude-sonnet-4-6")
    api_key = ai_cfg.get("api_key", "")
    max_tokens = int(ai_cfg.get("max_tokens", 8192))

    report_dir = Path(config.get("output", {}).get("report_dir", "./data"))
    report_dir.mkdir(parents=True, exist_ok=True)
    prefix = config.get("output", {}).get("report_prefix", "주가자금동향")
    filepath = report_dir / f"{prefix}_{base_date}.md"

    logger.info(f"AI 보고서 생성 중... (provider={provider}, model={model}, max_tokens={max_tokens})")

    # 공유 데이터 컨텍스트 구성 (1회)
    ctx = _build_data_context(processed, chart_paths, rotation_data)

    # 3개 파트 순차 호출
    parts = [
        ("파트1 (섹션 1-2: 시장요약+외국인)", _build_prompt_part1(ctx)),
        ("파트2 (섹션 3-4: 기관+섹터로테이션)", _build_prompt_part2(ctx)),
        ("파트3 (섹션 5-7: 시총집중도+개인+시사점)", _build_prompt_part3(ctx)),
    ]

    # 파트별로 부착할 차트 (AI 출력이 잘려도 차트는 항상 보장됨)
    chart_appendix = [
        # 파트1 끝 (외국인 자금 추이)
        [
            ("KOSPI 주간 외국인·기관 자금 추이", "flow_kospi"),
            ("KOSDAQ 주간 외국인·기관 자금 추이", "flow_kosdaq"),
        ],
        # 파트2 끝 (섹터별 자금 동향)
        [
            ("KOSPI 섹터별 주간 자금 동향", "sector_flow_kospi"),
            ("KOSDAQ 섹터별 주간 자금 동향", "sector_flow_kosdaq"),
        ],
        # 파트3 끝 (시총 집중도 추이)
        [
            ("KOSPI 시총비중 추이", "cap_weight_kospi"),
            ("KOSDAQ 시총비중 추이", "cap_weight_kosdaq"),
        ],
    ]

    sections = []
    for idx, (part_name, prompt) in enumerate(parts):
        logger.info(f"  {part_name} 생성 중...")
        try:
            text = _call_ai(prompt, provider, model, api_key, max_tokens)
            text = text.strip()
            logger.info(f"  {part_name} 완료 ({len(text):,}자)")
        except Exception as e:
            logger.error(f"  {part_name} AI 호출 실패: {e}")
            text = f"<!-- {part_name} 생성 실패: {e} -->\n"

        # 결정론적 차트 삽입 (AI 출력 길이와 무관하게 항상 포함)
        text = _append_charts(text, ctx["base_date"], chart_appendix[idx])
        sections.append(text)

    report_text = "\n\n---\n\n".join(sections)

    # ETF 수급 섹션 (결정적 — LLM 미사용). 실패해도 본문은 유지.
    try:
        report_text = _append_etf_section(report_text, processed)
    except Exception:
        logger.exception("ETF 섹션 삽입 실패 (본문 유지)")

    # Obsidian LLM-wiki 메타데이터 표준 frontmatter (수급동향 스키마)
    iso_date = f"{base_date[:4]}-{base_date[4:6]}-{base_date[6:8]}" if len(base_date) == 8 else base_date
    frontmatter = (
        "---\n"
        f'title: "{prefix}_{base_date}"\n'
        "type: output\n"
        f"created: {iso_date}\n"
        f"updated: {iso_date}\n"
        "tags:\n"
        "  - 시황\n"
        "  - 수급\n"
        "doc_type: 수급동향\n"
        "---\n"
    )
    report_text = frontmatter + report_text

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(report_text)

    logger.info(f"보고서 저장 완료: {filepath} ({len(report_text):,}자)")
    return str(filepath)
