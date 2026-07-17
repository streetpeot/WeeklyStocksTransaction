# ETF 수급 섹션 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** KRX ETF 투자자 순매수를 별도 수집해 공유용·개인용 보고서 공통 본문에 결정적 「ETF 수급」 섹션(시장 집계 + 외국인·기관 순매수·순매도 TOP5)을 추가한다.

**Architecture:** crawler에 ETF 전용 수집(`get_etf_trading_volume_and_value`, 시장 집계 1콜 + 개별 ETF 루프, best-effort)을 추가하고, processor가 pass-through, database가 `weekly_stock`에 `market="ETF"`로 축적, 신규 `etf_section.py`가 결정적 markdown을 렌더, reporter가 LLM 본문 뒤에 삽입한다. 개인용은 공유용 파일을 상속하므로 ETF 섹션은 양쪽에, 워치리스트는 개인용에만 남는다.

**Tech Stack:** Python 3.11, pykrx 1.2.8(KRX 로그인), pandas, SQLite, pytest. 스펙: `docs/superpowers/specs/2026-07-16-etf-flows-section-design.md`

## Global Constraints

- **작업 위치**: git worktree `/Volumes/삼성SSD_2TB/Agents/WST-full-market-flows`(브랜치 feature/full-market-flows). 프로덕션 체크아웃 `/Volumes/삼성SSD_2TB/Agents/WeeklyStocksTransaction` 브랜치 전환 금지.
- **배포 게이트**: 전종목 수급과 통합 — 2026-07-17(금) 20시 첫 무인 발행 성공 확인 후 main 병합(기존 full-market-flows 계획 Task 10).
- **발행 계약 불변**: 공유용 파일명 `주가자금동향_YYYYMMDD.md`·frontmatter·차트 `*_기준일.png`. ETF 섹션은 공통 본문에 **추가**될 뿐 계약 필드 무변경.
- **소스**: ETF 수급은 pykrx `get_etf_trading_volume_and_value`만 사용(반환: index=투자자, columns=MultiIndex ("거래량"|"거래대금")×("매도"|"매수"|"순매수")). `("거래대금","순매수")`가 순매수거래대금(원) → /1e8로 억원. `기관합계`·`외국인` 인덱스 라벨 사용.
- **이름 조회**: `get_etf_ticker_list(date)`를 먼저 호출하면 `get_etf_ticker_name(t)`는 로컬(0ms) — 루프 내 이름 조회 무비용.
- **테스트 명령**: `PYTHONPATH=".venv/lib/python3.11/site-packages:." /opt/homebrew/bin/python3.11 -m pytest tests/ -v` — 현재 48건 + 신규 상시 통과.
- **실API 금지(단위 테스트)**: pykrx 전부 mock. 실호출은 Task 5 라이브 리허설에서만.
- 커밋 메시지 끝: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`

## File Structure

| 파일 | 역할 |
|---|---|
| Modify `modules/crawler.py` | `crawl_krx_etf_flows()` 신설 + `collect_all()`에 통합 |
| Modify `modules/processor.py` | `etf_flows`·`etf_market_agg` pass-through |
| Modify `modules/database.py` | `_upsert_stock`이 ETF도 저장(market="ETF") |
| Create `modules/etf_section.py` | ETF 수급 섹션 결정적 렌더 |
| Modify `modules/reporter.py` | `_append_etf_section` 헬퍼 + generate_report 삽입 |
| Test `tests/test_etf_flows.py`, `tests/test_etf_section.py`, `tests/test_reporter_etf.py`, 기존 `tests/test_database_flows.py` 확장 | |

---

### Task 1: crawler — ETF 수급 수집 + collect_all 통합

**Files:**
- Modify: `modules/crawler.py` (`crawl_krx_investor_flows` 정의 아래에 신규 함수; `collect_all`의 KRX 블록 §5.5 뒤에 통합)
- Test: `tests/test_etf_flows.py`

**Interfaces:**
- Produces: `crawl_krx_etf_flows(fromdate: str, todate: str, time_budget: float = 300.0) -> tuple[pd.DataFrame, dict | None]` — `(etf_flows, etf_market_agg)`. `etf_flows` 컬럼 `[티커, 종목명, 1주기관매매, 1주외국인매매]`(억원, float). `etf_market_agg` = `{"외국인": float, "기관": float, "개인": float}` 또는 None. best-effort(개별 실패 스킵, time_budget 초과 중단, 집계 실패 None, 전체 빈 → 빈 DataFrame).
- Produces: `collect_all()` 반환 dict에 `etf_flows`(DataFrame), `etf_market_agg`(dict|None) 추가.
- Consumes: pykrx `stock.get_etf_trading_volume_and_value`, `stock.get_etf_ticker_list`, `stock.get_etf_ticker_name`.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/test_etf_flows.py
from unittest.mock import patch

import pandas as pd
import pytest

from modules import crawler


def _investor_df(net_won: dict) -> pd.DataFrame:
    """get_etf_trading_volume_and_value 반환 형태 재현: index=투자자, columns MultiIndex, ('거래대금','순매수')만 채움."""
    idx = pd.Index(list(net_won.keys()), name="INVST_NM")
    df = pd.DataFrame({("거래대금", "순매수"): list(net_won.values())}, index=idx)
    df.columns = pd.MultiIndex.from_tuples(df.columns)
    return df


def test_crawl_etf_flows_market_agg_and_per_ticker():
    agg_df = _investor_df({"기관합계": -19_568e8, "외국인": 5e8, "개인": 19_048e8})
    per = {
        "069500": _investor_df({"기관합계": -100e8, "외국인": 50e8}),
        "229200": _investor_df({"기관합계": -1_171e8, "외국인": 543e8}),
    }

    def fake(fromdate, todate, *args):
        # (from,to) → 시장 집계 / (from,to,ticker) → 개별
        if not args:
            return agg_df
        return per[args[0]]

    with patch("pykrx.stock.get_etf_trading_volume_and_value", side_effect=fake), \
         patch("pykrx.stock.get_etf_ticker_list", return_value=["069500", "229200"]), \
         patch("pykrx.stock.get_etf_ticker_name", side_effect=lambda t: {"069500": "KODEX 200", "229200": "KODEX 코스닥150"}[t]):
        flows, agg = crawler.crawl_krx_etf_flows("20260706", "20260710")

    assert agg == {"외국인": pytest.approx(5.0), "기관": pytest.approx(-19568.0), "개인": pytest.approx(19048.0)}
    assert sorted(flows.columns) == sorted(["티커", "종목명", "1주기관매매", "1주외국인매매"])
    row = flows[flows["티커"] == "229200"].iloc[0]
    assert row["종목명"] == "KODEX 코스닥150"
    assert row["1주외국인매매"] == pytest.approx(543.0)
    assert row["1주기관매매"] == pytest.approx(-1171.0)


def test_crawl_etf_flows_skips_failed_ticker():
    def fake(fromdate, todate, *args):
        if not args:
            raise Exception("agg fail")           # 집계 실패 → None
        if args[0] == "BAD":
            raise Exception("ticker fail")          # 개별 실패 → 스킵
        return _investor_df({"기관합계": 1e8, "외국인": 2e8})

    with patch("pykrx.stock.get_etf_trading_volume_and_value", side_effect=fake), \
         patch("pykrx.stock.get_etf_ticker_list", return_value=["BAD", "069500"]), \
         patch("pykrx.stock.get_etf_ticker_name", side_effect=lambda t: "이름"):
        flows, agg = crawler.crawl_krx_etf_flows("20260706", "20260710")

    assert agg is None
    assert list(flows["티커"]) == ["069500"]      # BAD 스킵
```

- [ ] **Step 2: 실패 확인**

Run: `PYTHONPATH=".venv/lib/python3.11/site-packages:." /opt/homebrew/bin/python3.11 -m pytest tests/test_etf_flows.py -v`
Expected: FAIL — `AttributeError: module 'modules.crawler' has no attribute 'crawl_krx_etf_flows'`

- [ ] **Step 3: 함수 구현** (`crawl_krx_investor_flows` 정의 끝 아래에 추가)

```python
# ─────────────────────────────────────────
# KRX ETF 투자자 순매수 (시장 집계 1콜 + 개별 ETF 루프)
# ─────────────────────────────────────────

def crawl_krx_etf_flows(fromdate: str, todate: str, time_budget: float = 300.0):
    """KRX ETF 투자자 순매수 — 시장 전체 집계(1콜) + 개별 ETF 티커별 순매수.

    Returns: (etf_flows[티커,종목명,1주기관매매,1주외국인매매], etf_market_agg{외국인,기관,개인}|None)
    best-effort: 개별 티커 예외 스킵, time_budget(초) 초과 시 루프 중단, 집계 실패 시 None.
    거래대금 기준(억원). ETF 미거래 종목은 행 없음.
    """
    import time as _time

    from pykrx import stock  # 지연 import — 테스트에서 mock 대상

    # 시장 전체 집계 (1콜)
    agg = None
    try:
        m = stock.get_etf_trading_volume_and_value(fromdate, todate)
        agg = {}
        for key, label in [("외국인", "외국인"), ("기관", "기관합계"), ("개인", "개인")]:
            if label in m.index:
                agg[key] = round(float(m.loc[label, ("거래대금", "순매수")]) / 1e8, 2)
    except Exception as e:
        logger.warning(f"ETF 시장 집계 실패: {e}")
        agg = None

    # 개별 ETF 루프 (get_etf_ticker_list가 이름 캐시 워밍 → get_etf_ticker_name 로컬)
    tickers = stock.get_etf_ticker_list(todate)
    rows = []
    t0 = _time.time()
    for i, t in enumerate(tickers):
        if _time.time() - t0 > time_budget:
            logger.warning(f"ETF 루프 time_budget({time_budget}s) 초과 — {i}/{len(tickers)}에서 중단")
            break
        try:
            df = stock.get_etf_trading_volume_and_value(fromdate, todate, t)
            if df is None or df.empty:
                continue
            inst = round(float(df.loc["기관합계", ("거래대금", "순매수")]) / 1e8, 2) if "기관합계" in df.index else None
            fore = round(float(df.loc["외국인", ("거래대금", "순매수")]) / 1e8, 2) if "외국인" in df.index else None
            rows.append({"티커": t, "종목명": stock.get_etf_ticker_name(t),
                         "1주기관매매": inst, "1주외국인매매": fore})
        except Exception:
            continue
        if (i + 1) % 200 == 0:
            logger.info(f"ETF 수급 수집: {i + 1}/{len(tickers)}")

    etf_flows = pd.DataFrame(rows) if rows else pd.DataFrame()
    logger.info(f"KRX ETF 수급 수집 완료: {len(etf_flows)}개 ETF (거래대금 기준)")
    return etf_flows, agg
```

- [ ] **Step 4: collect_all 통합** — `collect_all()`의 §5.5(KRX 전종목 수급) 블록 뒤에 삽입:

```python
    # ── 5.6 KRX ETF 수급 (시장 집계 + 개별 ETF, best-effort) ──
    try:
        etf_flows, etf_agg = crawl_krx_etf_flows(monday_str, base_str)
        result["etf_flows"] = etf_flows
        result["etf_market_agg"] = etf_agg
    except Exception as e:
        logger.warning(f"ETF 수급 수집 실패({e}) → ETF 섹션 생략")
        result["etf_flows"] = pd.DataFrame()
        result["etf_market_agg"] = None
```

- [ ] **Step 5: collect_all 통합 테스트 추가** (`tests/test_etf_flows.py`에 append — 실제 collect_all 실행, 수집 함수 patch로 네트워크 회피)

```python
def test_collect_all_wires_etf(monkeypatch):
    def fake_market(market_code, max_pages=None):
        return pd.DataFrame({"티커": ["005930"], "종목명": ["삼성전자"], "시가총액(억)": [100.0]})

    etf_flows = pd.DataFrame({"티커": ["069500"], "종목명": ["KODEX 200"],
                              "1주기관매매": [-100.0], "1주외국인매매": [50.0]})
    with patch("modules.crawler.crawl_naver_market", side_effect=fake_market), \
         patch("modules.crawler.build_sector_map", return_value={}), \
         patch("modules.crawler.collect_kis_investor_rank", return_value={}), \
         patch("modules.crawler.collect_kis_market_info", return_value={}), \
         patch("modules.crawler.crawl_period_returns_all", return_value=pd.DataFrame()), \
         patch("modules.crawler.crawl_naver_stock_details", return_value=pd.DataFrame()), \
         patch("modules.crawler.crawl_krx_investor_flows", return_value=pd.DataFrame()), \
         patch("modules.crawler.crawl_krx_etf_flows", return_value=(etf_flows, {"외국인": 5.0})):
        result = crawler.collect_all({"kis": {"app_key": "", "app_secret": ""}})

    assert list(result["etf_flows"]["티커"]) == ["069500"]
    assert result["etf_market_agg"] == {"외국인": 5.0}
```

- [ ] **Step 6: 전체 스위트 통과 확인**

Run: `PYTHONPATH=".venv/lib/python3.11/site-packages:." /opt/homebrew/bin/python3.11 -m pytest tests/ -v`
Expected: 전체 passed (기존 48 + 신규 3 = 51)

- [ ] **Step 7: Commit**

```bash
git add modules/crawler.py tests/test_etf_flows.py
git commit -m "feat: KRX ETF 수급 수집 (시장 집계 + 개별 ETF 루프, best-effort)"
```

---

### Task 2: processor pass-through + database ETF 축적

**Files:**
- Modify: `modules/processor.py` (`process()` 말미 pass-through)
- Modify: `modules/database.py` (`_upsert_stock`에 ETF 저장)
- Test: `tests/test_database_flows.py` (기존 파일에 추가)

**Interfaces:**
- Consumes: `raw["etf_flows"]`(DataFrame), `raw["etf_market_agg"]`(dict|None) — Task 1 산출.
- Produces: `processed["etf_flows"]`, `processed["etf_market_agg"]`. `weekly_stock`에 `market="ETF"`, `flow_source="krx"` 행 저장.

- [ ] **Step 1: processor pass-through 구현** — `process()` 말미 `processed["flow_source"] = ...` 다음 줄에 추가:

```python
    processed["etf_flows"] = raw.get("etf_flows", pd.DataFrame())
    processed["etf_market_agg"] = raw.get("etf_market_agg")
```

- [ ] **Step 2: 실패하는 DB 테스트 작성** (`tests/test_database_flows.py`에 append)

```python
def test_upsert_stock_persists_etf(db):
    processed = {
        "kospi": pd.DataFrame(), "kosdaq": pd.DataFrame(),
        "flow_source": "krx",
        "etf_flows": pd.DataFrame({
            "티커": ["069500"], "종목명": ["KODEX 200"],
            "1주기관매매": [-100.0], "1주외국인매매": [50.0],
        }),
    }
    with db._connect() as conn:
        db._upsert_stock(conn, "20260717", processed)
        row = conn.execute(
            "SELECT market, inst_net_1w, foreign_net_1w, flow_source "
            "FROM weekly_stock WHERE ticker='069500'"
        ).fetchone()
    assert row == ("ETF", -100.0, 50.0, "krx")


def test_etf_rows_inert_to_market_queries(db):
    """ETF 행은 KOSPI/KOSDAQ 조회에 영향 없음."""
    processed = {
        "kospi": pd.DataFrame({"티커": ["005930"], "1주기관매매": [1.0], "1주외국인매매": [2.0]}),
        "kosdaq": pd.DataFrame(),
        "flow_source": "krx",
        "etf_flows": pd.DataFrame({"티커": ["069500"], "종목명": ["KODEX 200"],
                                   "1주기관매매": [-100.0], "1주외국인매매": [50.0]}),
    }
    with db._connect() as conn:
        db._upsert_stock(conn, "20260717", processed)
    acc = db.get_stock_investor_accumulate("KOSPI", 1)
    assert list(acc["ticker"]) == ["005930"]     # ETF 미포함
```

- [ ] **Step 3: 실패 확인**

Run: `PYTHONPATH=".venv/lib/python3.11/site-packages:." /opt/homebrew/bin/python3.11 -m pytest tests/test_database_flows.py -k etf -v`
Expected: FAIL (ETF 행 미저장 — `row is None`)

- [ ] **Step 4: database 구현** — `_upsert_stock`의 KOSPI/KOSDAQ 루프 뒤에 ETF 저장 추가. 메서드 서두의 `flow_source = processed.get("flow_source", "naver")` 재사용:

```python
        # ETF 수급 (market="ETF") — 기존 KOSPI/KOSDAQ 조회는 market 필터라 무해
        etf = processed.get("etf_flows", pd.DataFrame())
        if isinstance(etf, pd.DataFrame) and not etf.empty:
            for _, row in etf.iterrows():
                ticker = row.get("티커")
                if not ticker:
                    continue
                inst = row.get("1주기관매매")
                fore = row.get("1주외국인매매")
                inst_val = float(inst) if inst is not None and pd.notna(inst) else None
                fore_val = float(fore) if fore is not None and pd.notna(fore) else None
                if inst_val is None and fore_val is None:
                    continue
                conn.execute(
                    """
                    INSERT OR REPLACE INTO weekly_stock
                    (week_date, market, ticker, inst_net_1w, foreign_net_1w, flow_source)
                    VALUES (?, 'ETF', ?, ?, ?, ?)
                    """,
                    (week_date, ticker, inst_val, fore_val, flow_source),
                )
```

- [ ] **Step 5: 통과 확인 (전체 스위트)**

Run: `PYTHONPATH=".venv/lib/python3.11/site-packages:." /opt/homebrew/bin/python3.11 -m pytest tests/ -v`
Expected: 전체 passed (51 + 신규 2 = 53)

- [ ] **Step 6: Commit**

```bash
git add modules/processor.py modules/database.py tests/test_database_flows.py
git commit -m "feat: ETF 수급 processor pass-through + weekly_stock 축적(market=ETF)"
```

---

### Task 3: etf_section — 결정적 섹션 렌더

**Files:**
- Create: `modules/etf_section.py`
- Test: `tests/test_etf_section.py`

**Interfaces:**
- Consumes: `etf_flows`(DataFrame [티커, 종목명, 1주기관매매, 1주외국인매매]), `etf_market_agg`(dict|None).
- Produces: `build_etf_section(etf_flows, etf_market_agg, n: int = 5) -> str` — `"## ETF 수급"`로 시작하는 markdown, 빈 입력 → `""`.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/test_etf_section.py
import pandas as pd

from modules import etf_section


def _flows():
    return pd.DataFrame({
        "티커": ["229200", "069500", "379800", "252670", "233740"],
        "종목명": ["KODEX 코스닥150", "KODEX 200", "KODEX 미국S&P500", "KODEX 200선물인버스2X", "KODEX 코스닥150레버리지"],
        "1주외국인매매": [543.0, 120.0, -80.0, -250.0, 30.0],
        "1주기관매매": [-1171.0, 300.0, 50.0, 40.0, -90.0],
    })


def test_empty_returns_blank():
    assert etf_section.build_etf_section(pd.DataFrame(), None) == ""
    assert etf_section.build_etf_section(None, None) == ""


def test_section_has_heading_agg_and_directions():
    md = etf_section.build_etf_section(_flows(), {"외국인": 5.0, "기관": -19568.0, "개인": 19048.0})
    assert md.startswith("## ETF 수급")
    assert "ETF 시장 전체" in md and "외국인 +5억" in md
    for title in ["외국인 순매수 상위", "외국인 순매도 상위", "기관 순매수 상위", "기관 순매도 상위"]:
        assert title in md
    # 외국인 순매수 1위 = KODEX 코스닥150(+543)
    fore_buy = md.split("외국인 순매수 상위")[1].split("외국인 순매도 상위")[0]
    assert "KODEX 코스닥150" in fore_buy
    # 외국인 순매도 1위 = KODEX 200선물인버스2X(-250)
    fore_sell = md.split("외국인 순매도 상위")[1].split("기관 순매수 상위")[0]
    assert "KODEX 200선물인버스2X" in fore_sell


def test_agg_omitted_when_none():
    md = etf_section.build_etf_section(_flows(), None)
    assert "ETF 시장 전체" not in md
    assert "외국인 순매수 상위" in md      # 표는 유지
```

- [ ] **Step 2: 실패 확인**

Run: `PYTHONPATH=".venv/lib/python3.11/site-packages:." /opt/homebrew/bin/python3.11 -m pytest tests/test_etf_section.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'modules.etf_section'`

- [ ] **Step 3: 구현**

```python
# modules/etf_section.py
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
        "> 개별 ETF 기관·외국인 순매수(KRX 거래대금 기준, 억원). ETF 시장 전체 집계 + 순매수·순매도 상위.",
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
```

- [ ] **Step 4: 통과 확인 (전체 스위트)**

Run: `PYTHONPATH=".venv/lib/python3.11/site-packages:." /opt/homebrew/bin/python3.11 -m pytest tests/ -v`
Expected: 전체 passed (53 + 신규 3 = 56)

- [ ] **Step 5: Commit**

```bash
git add modules/etf_section.py tests/test_etf_section.py
git commit -m "feat: ETF 수급 섹션 결정적 렌더 (시장집계+4방향 TOP5+무브머 카피)"
```

---

### Task 4: reporter — ETF 섹션 삽입

**Files:**
- Modify: `modules/reporter.py` (`_append_etf_section` 헬퍼 + `generate_report`에서 호출)
- Test: `tests/test_reporter_etf.py`

**Interfaces:**
- Consumes: `etf_section.build_etf_section` (Task 3), `processed["etf_flows"]`/`["etf_market_agg"]` (Task 2).
- Produces: `_append_etf_section(report_text: str, processed: dict) -> str` — ETF 섹션이 있으면 `report_text + "\n\n---\n\n" + 섹션`, 없으면 원본. `generate_report`가 frontmatter prepend 직전에 호출(공유용 파일에 포함 → 개인용 상속).

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/test_reporter_etf.py
import pandas as pd

from modules import reporter


def _processed(with_etf=True):
    p = {"etf_market_agg": {"외국인": 5.0}}
    p["etf_flows"] = pd.DataFrame({
        "티커": ["229200"], "종목명": ["KODEX 코스닥150"],
        "1주외국인매매": [543.0], "1주기관매매": [-1171.0],
    }) if with_etf else pd.DataFrame()
    return p


def test_append_etf_section_adds_when_present():
    out = reporter._append_etf_section("본문", _processed(with_etf=True))
    assert "본문" in out
    assert "## ETF 수급" in out
    assert "KODEX 코스닥150" in out


def test_append_etf_section_noop_when_empty():
    out = reporter._append_etf_section("본문", _processed(with_etf=False))
    assert out == "본문"
```

- [ ] **Step 2: 실패 확인**

Run: `PYTHONPATH=".venv/lib/python3.11/site-packages:." /opt/homebrew/bin/python3.11 -m pytest tests/test_reporter_etf.py -v`
Expected: FAIL — `AttributeError: module 'modules.reporter' has no attribute '_append_etf_section'`

- [ ] **Step 3: 구현** — `reporter.py`에 헬퍼 추가(`generate_report` 정의 앞):

```python
def _append_etf_section(report_text: str, processed: dict) -> str:
    """ETF 수급 섹션을 본문 뒤에 결정적 삽입(있을 때만). 공유용에 포함 → 개인용 상속."""
    from modules import etf_section
    etf_md = etf_section.build_etf_section(
        processed.get("etf_flows"), processed.get("etf_market_agg"))
    if etf_md:
        return report_text.rstrip() + "\n\n---\n\n" + etf_md
    return report_text
```

`generate_report`에서 `report_text = "\n\n---\n\n".join(sections)` 다음, frontmatter prepend 앞에 삽입:

```python
    report_text = "\n\n---\n\n".join(sections)

    # ETF 수급 섹션 (결정적 — LLM 미사용). 실패해도 본문은 유지.
    try:
        report_text = _append_etf_section(report_text, processed)
    except Exception:
        logger.exception("ETF 섹션 삽입 실패 (본문 유지)")
```

- [ ] **Step 4: 통과 확인 (전체 스위트)**

Run: `PYTHONPATH=".venv/lib/python3.11/site-packages:." /opt/homebrew/bin/python3.11 -m pytest tests/ -v`
Expected: 전체 passed (56 + 신규 2 = 58)

- [ ] **Step 5: Commit**

```bash
git add modules/reporter.py tests/test_reporter_etf.py
git commit -m "feat: 보고서 본문에 ETF 수급 섹션 삽입 (공유용·개인용 공통)"
```

---

### Task 5: 라이브 리허설 (worktree — ETF 포함 전체 실행)

**Files:** 없음 (검증만)

- [ ] **Step 1: 실데이터 미드위크 실행** (발행 자동 생략 — 채널 무오염. LLM 1회 + ETF 루프 실측)

```bash
cd /Volumes/삼성SSD_2TB/Agents/WST-full-market-flows
PYTHONPATH=".venv/lib/python3.11/site-packages:." .venv/bin/python main.py --midweek 2>&1 | tee /tmp/etf-rehearsal.log
```
Expected: 로그에 `KRX ETF 수급 수집 완료: ~1,100개 ETF`. **ETF 루프 실제 소요 시간 확인**(로그 타임스탬프로 [1/6] 내 ETF 구간 측정 — time_budget 300초 내). 전체 실행 시간이 현행(~11분) 대비 +1~3분 수준인지 확인.

- [ ] **Step 2: ETF 섹션이 양쪽 보고서에 들어갔는지 검증**

```bash
cd /Volumes/삼성SSD_2TB/Agents/WST-full-market-flows
D=$(date +%Y%m%d)
echo "=== 공유용 ETF 섹션 ==="; grep -c "## ETF 수급" "data/주가자금동향_${D}.md"
echo "=== 개인용 ETF+워치리스트 ==="; grep -c "## ETF 수급\|## 워치리스트 수급" "data/주가자금동향_개인_${D}.md"
echo "=== 공유용에 워치리스트 없음(0) ==="; grep -c "워치리스트" "data/주가자금동향_${D}.md"
echo "=== ETF 섹션 미리보기 ==="; sed -n '/## ETF 수급/,/^## /p' "data/주가자금동향_${D}.md" | head -30
echo "=== weekly_stock ETF 적재 ==="; PYTHONPATH=".venv/lib/python3.11/site-packages" .venv/bin/python -c "import sqlite3; print(sqlite3.connect('history.db').execute(\"SELECT COUNT(*) FROM weekly_stock WHERE market='ETF'\").fetchone())"
```
Expected: 공유용 ETF 섹션 1개, 개인용 ETF+워치리스트 2개, 공유용 워치리스트 0개, ETF 섹션에 시장 집계 + 4방향 표, weekly_stock ETF 행 수백~천 개.

- [ ] **Step 3: 사용자 보고** — ETF 루프 실측 시간·전체 실행 시간·ETF 섹션 모양·양쪽 반영을 사용자에게 보고. time_budget 초과나 과도한 지연이 있으면 유동성 필터(범위 외 §8) 재논의.

---

## Self-Review 결과 (작성 시 수행)

- 스펙 커버리지: §4.1(Task 1) §4.2(Task 1) §4.3(Task 2) §4.4(Task 3) §4.5(Task 4) §4.6(Task 2) §5 제약(Global Constraints·Task 5) §6 테스트(각 Task) §7 수용 기준(Task 5) — 공백 없음.
- 타입 일관성: `crawl_krx_etf_flows` 반환 `(etf_flows[티커,종목명,1주기관매매,1주외국인매매], agg{외국인,기관,개인}|None)` = processor 소비 = etf_section 소비 = database 소비 컬럼 일치. `build_etf_section(etf_flows, etf_market_agg, n=5)` Task 3 정의 = Task 4 `_append_etf_section` 호출 일치. `_append_etf_section(report_text, processed)` Task 4 정의 = generate_report 호출 일치.
- 배포: 별도 Task 없음 — 전종목 수급 계획 Task 10(배포)이 두 기능을 함께 병합. 이 계획 완료 후 그 게이트로 수렴.
