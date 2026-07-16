"""워치리스트 — 볼트 md 표 파싱 + 개인용 보고서(워치리스트 수급 섹션) 생성.

공유용 보고서와의 문맥 격리(스펙 §4.4): 이 모듈은 LLM을 호출하지 않고,
워치리스트 데이터는 reporter의 프롬프트에 진입하지 않는다.
공유용 파일은 무변형(개인용은 본문 재사용 후 섹션 append) — 원본을 직접 수정하지 않는다.
"""
import logging
import re
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

_TICKER_RE = re.compile(r"^\d{6}$")


def load_watchlist(path) -> pd.DataFrame:
    """볼트 워치리스트 md의 표에서 [티커, 종목명, 구분] 추출.

    표 형식: | 티커 | 종목명 | 구분 | 메모 | — 티커 셀이 6자리 숫자인 행만 채택.
    파일 없음/표 없음/파싱 실패 → 빈 DataFrame (파이프라인은 계속).
    """
    p = Path(path)
    if not p.exists():
        logger.warning(f"워치리스트 파일 없음: {p}")
        return pd.DataFrame()
    rows = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip().startswith("|"):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) >= 2 and _TICKER_RE.match(cells[0]):
                rows.append({
                    "티커": cells[0],
                    "종목명": cells[1] if len(cells) > 1 else "",
                    "구분": cells[2] if len(cells) > 2 else "",
                })
    except Exception as e:
        logger.warning(f"워치리스트 파싱 실패({e}) — 섹션 생략")
        return pd.DataFrame()
    return pd.DataFrame(rows)


def _fmt(v, signed=True) -> str:
    if v is None or pd.isna(v):
        return "-"
    return f"{v:+,.1f}" if signed else f"{v:,.1f}"


def _trend(vals: list) -> str:
    """주간 값 나열: 오래된 → 최신. 예: '+2.0 → +1.5'"""
    shown = [_fmt(v) for v in vals]
    return " → ".join(shown) if shown else "-"


def build_watchlist_section(wl: pd.DataFrame, processed: dict, db) -> str:
    """워치리스트 수급 섹션 markdown 생성 (결정적 — LLM 미사용)."""
    lines = [
        "## 워치리스트 수급",
        "",
        "> 개인용 섹션 — 기관·외국인 순매수는 KRX 거래대금 기준(억원), 4주 추이는 오래된 주 → 최신 주.",
        "",
        "| 종목 | 구분 | 시장 | 1주 등락률 | 기관(억) | 외국인(억) | 기관 4주 추이 | 외국인 4주 추이 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    combined = pd.concat(
        [processed.get("kospi", pd.DataFrame()), processed.get("kosdaq", pd.DataFrame())],
        ignore_index=True,
    )
    if combined.empty:
        return "\n".join(lines + ["| (데이터 없음) | | | | | | | |"]) + "\n"
    combined = combined.drop_duplicates(subset="티커", keep="first")
    combined = combined.set_index("티커")

    for _, w in wl.iterrows():
        t = w["티커"]
        if t in combined.index:
            row = combined.loc[t]
            market = str(row.get("시장", ""))
            hist = db.get_stock_flow_history(t, market, n_weeks=4)
            inst_trend = _trend(list(hist["inst_net_1w"])) if not hist.empty else "-"
            fore_trend = _trend(list(hist["foreign_net_1w"])) if not hist.empty else "-"
            ret = row.get("1주등락률")
            lines.append(
                f"| {row.get('종목명', w['종목명'])} | {w['구분']} | {market} "
                f"| {_fmt(ret)}% | {_fmt(row.get('1주기관매매'))} | {_fmt(row.get('1주외국인매매'))} "
                f"| {inst_trend} | {fore_trend} |"
            )
        else:
            lines.append(f"| {w['종목명']}({t}) | {w['구분']} | - | - | - | - | - | - |")
    return "\n".join(lines) + "\n"


def generate_personal_report(report_path: str, processed: dict, db, config: dict) -> Optional[str]:
    """공유용 보고서 + 워치리스트 섹션 → 개인용 md 저장. 워치리스트 없으면 None."""
    wl_path = config.get("publish", {}).get("watchlist_path", "")
    if not wl_path:
        logger.info("watchlist_path 미설정 — 개인용 보고서 생략")
        return None
    wl = load_watchlist(wl_path)
    if wl.empty:
        logger.warning("워치리스트 비어 있음 — 개인용 보고서 생략")
        return None

    src = Path(report_path)
    section = build_watchlist_section(wl, processed, db)
    text = src.read_text(encoding="utf-8").rstrip() + "\n\n---\n\n" + section

    prefix = config.get("output", {}).get("report_prefix", "주가자금동향")
    dest = src.with_name(src.name.replace(f"{prefix}_", f"{prefix}_개인_", 1))
    if dest == src:
        logger.warning(f"개인용 파일명 유도 실패(prefix 불일치: {src.name}) — 개인용 생략")
        return None
    dest.write_text(text, encoding="utf-8")
    logger.info(f"개인용 보고서 저장: {dest} (워치리스트 {len(wl)}종목)")
    return str(dest)
