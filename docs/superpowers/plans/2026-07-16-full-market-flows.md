# 전종목 수급 확보 + 보고서 이원화 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** KRX 일괄 API로 전종목 기관·외국인 주간 순매수를 수집하고(실패 시 현행 Naver 폴백), 볼트 워치리스트 기반 개인용 보고서와 현행 공유용 보고서로 이원화해 발행한다.

**Architecture:** crawler에 KRX 일괄 수집(주 4콜)을 추가하고 processor에서 수급 컬럼을 KRX 값으로 전면 대체(폴백 시 현행 유지). 보고서는 공유용(현행 LLM 3-part) 생성 후 워치리스트 섹션(결정적 테이블, LLM 미사용)을 후처리로 붙여 개인용을 만든다. publisher가 볼트·DM=개인용 / 채널=공유용으로 라우팅한다.

**Tech Stack:** Python 3.11, pykrx 1.2.8(KRX 로그인), pandas, SQLite, pytest. 스펙: `docs/superpowers/specs/2026-07-16-full-market-flows-design.md`

## Global Constraints

- **프로덕션 체크아웃 보호**: `/Volumes/삼성SSD_2TB/Agents/WeeklyStocksTransaction`은 launchd가 그대로 실행하는 프로덕션. **이 디렉토리에서 브랜치 전환 금지.** 모든 코드 작업은 git worktree에서 (Task 1에서 생성).
- **병합 게이트**: 2026-07-17(금) 20시 발행 자동화 첫 무인 실행 성공 확인 전 main 병합 금지. Task 10(배포)은 이 확인 후에만.
- **발행 계약 불변**: 공유용 파일명 `주가자금동향_YYYYMMDD.md`, frontmatter 구조, 차트 `*_기준일.png` 패턴 유지.
- **테스트 명령(worktree 내)**: `PYTHONPATH=".venv/lib/python3.11/site-packages:." /opt/homebrew/bin/python3.11 -m pytest tests/ -v` — 기존 24건 + 신규 전체 상시 통과.
- **시크릿**: config.yaml·.kis_token_cache.json은 gitignore(커밋 금지). KRX 자격증명은 키체인 서비스 `krx-data`만. 채팅·코드·로그에 비밀번호 평문 금지.
- **의존성**: `pykrx>=1.2.8` (requirements.txt에서 `>=1.0.35` 교체).
- **KRX 실패 모드**: pykrx는 로그인 실패 시 예외가 아니라 **빈 DataFrame**을 반환한다. 빈 응답 검사로 폴백을 트리거할 것.
- 커밋 메시지 끝: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`

## 사전 조건 (사용자 액션 — Task 1 전 완료 필요)

1. data.krx.co.kr(KRX 정보데이터시스템) 무료 계정 가입.
2. 키체인 등록 (터미널에서 사용자가 직접 실행, `<KRX_ID>`/`<KRX_PW>`를 본인 값으로):
   ```bash
   security add-generic-password -s krx-data -a "<KRX_ID>" -w "<KRX_PW>"
   ```

## File Structure

| 파일 | 역할 |
|---|---|
| Create `modules/krx_auth.py` | 키체인 → KRX_ID/KRX_PW 환경변수 주입 |
| Create `modules/watchlist.py` | 워치리스트 파싱 + 섹션 렌더 + 개인용 보고서 생성 |
| Create `scripts/verify_krx.py` | 검증 게이트 스크립트 (수동 실행) |
| Modify `modules/crawler.py` | `crawl_krx_investor_flows()` 신설 + `collect_all()`에 통합 |
| Modify `modules/processor.py` | KRX 수급으로 컬럼 대체(폴백 시 현행) |
| Modify `modules/database.py` | `flow_source` 컬럼 마이그레이션 + `get_stock_flow_history()` |
| Modify `modules/publisher.py` | `personal_path` 라우팅 (볼트·DM=개인용 / 채널=공유용) |
| Modify `main.py` | 자격증명 주입·폴백 DM 통지·개인용 생성·publish 연결 |
| Modify `requirements.txt` | pykrx>=1.2.8 |
| Test `tests/test_krx_auth.py`, `tests/test_krx_flows.py`, `tests/test_processor_flows.py`, `tests/test_database_flows.py`, `tests/test_watchlist.py`, 기존 `tests/test_publisher.py` 확장 | |

---

### Task 1: worktree 준비 + KRX 검증 게이트

**Files:**
- Create: `scripts/verify_krx.py` (worktree 내)

**Interfaces:**
- Produces: 검증 게이트 통과/실패 판정. **불성립 시 이후 Task 진행 중단, 사용자와 재논의.**

- [ ] **Step 1: worktree + 독립 venv 생성**

```bash
cd /Volumes/삼성SSD_2TB/Agents/WeeklyStocksTransaction
git worktree add ../WST-full-market-flows -b feature/full-market-flows
cd ../WST-full-market-flows
/opt/homebrew/bin/python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt 'pykrx>=1.2.8'
cp ../WeeklyStocksTransaction/config.yaml .   # gitignored 설정 복사 (커밋 금지)
```
Expected: worktree 생성, pykrx 1.2.8 설치. **이후 모든 Task는 이 worktree에서.**

- [ ] **Step 2: 기존 테스트 통과 확인 (베이스라인)**

Run: `PYTHONPATH=".venv/lib/python3.11/site-packages:." /opt/homebrew/bin/python3.11 -m pytest tests/ -v`
Expected: 24 passed

- [ ] **Step 3: 검증 게이트 스크립트 작성**

```python
# scripts/verify_krx.py
"""KRX 일괄 API 검증 게이트 — 스펙 §4.2. 수동 실행:
PYTHONPATH=".venv/lib/python3.11/site-packages:." .venv/bin/python scripts/verify_krx.py
사전: 키체인 krx-data 등록 (README 사전 조건 참조)
"""
import sys
from datetime import date, timedelta

sys.path.insert(0, ".")
from modules import krx_auth  # noqa: E402  (Task 2에서 구현 — 그 전엔 환경변수 직접 설정으로 실행 가능)


def last_full_week() -> tuple[str, str]:
    today = date.today()
    friday = today - timedelta(days=(today.weekday() - 4) % 7)
    if friday == today:
        friday -= timedelta(days=7)
    monday = friday - timedelta(days=4)
    return monday.strftime("%Y%m%d"), friday.strftime("%Y%m%d")


def main() -> int:
    import os
    if not (os.environ.get("KRX_ID") and os.environ.get("KRX_PW")):
        try:
            krx_auth.inject_credentials()
        except Exception as e:
            print(f"자격증명 주입 실패: {e}")
    if not (os.environ.get("KRX_ID") and os.environ.get("KRX_PW")):
        print("BLOCKED: 키체인 krx-data 미등록 — 사전 조건 수행 필요")
        return 2

    from pykrx import stock
    monday, friday = last_full_week()
    print(f"검증 주간: {monday}~{friday}")
    failures = []

    # ① 전종목 반환 여부 (순매수'상위'종목 화면이 전체를 주는지)
    for market, floor in [("KOSPI", 1500), ("KOSDAQ", 1400)]:
        df = stock.get_market_net_purchases_of_equities_by_ticker(monday, friday, market, "외국인")
        print(f"① {market} 외국인 rows={len(df)} (기준 ≥{floor})")
        if len(df) < floor:
            failures.append(f"{market} 전종목 미반환({len(df)}행)")

    # ② 소형주 포함 + 값 형태 (볼트 엔티티 중 시총 200위 밖)
    df_ksq = stock.get_market_net_purchases_of_equities_by_ticker(monday, friday, "KOSDAQ", "기관합계")
    for t, name in [("380540", "옵티코어"), ("200710", "에이디테크놀로지")]:
        if t in df_ksq.index:
            print(f"② {name}({t}) 기관 순매수: {df_ksq.loc[t, '순매수거래대금'] / 1e8:.1f}억")
        else:
            failures.append(f"{name}({t}) 미포함")

    # ③ 당일 데이터 반영 시각 — 오늘(거래일 저녁 실행 시) 데이터 존재 여부
    today_s = date.today().strftime("%Y%m%d")
    df_today = stock.get_market_net_purchases_of_equities_by_ticker(today_s, today_s, "KOSPI", "외국인")
    print(f"③ 당일({today_s}) 데이터: {len(df_today)}행 "
          f"{'— 당일 저녁 반영 확인' if len(df_today) else '— 미반영(장중/휴장이면 정상, 금 20시 재확인 필요)'}")

    # ④ 값 정합성 참고 출력 — 삼성전자 주간치 (Naver 보고서 값과 사람이 대조)
    if "005930" in stock.get_market_net_purchases_of_equities_by_ticker(monday, friday, "KOSPI", "기관합계").index:
        df_k = stock.get_market_net_purchases_of_equities_by_ticker(monday, friday, "KOSPI", "기관합계")
        df_f = stock.get_market_net_purchases_of_equities_by_ticker(monday, friday, "KOSPI", "외국인")
        print(f"④ 삼성전자 기관 {df_k.loc['005930', '순매수거래대금'] / 1e8:+.0f}억 / "
              f"외국인 {df_f.loc['005930', '순매수거래대금'] / 1e8:+.0f}억 "
              f"→ data/주가자금동향_{friday}.md 값과 부호·자릿수 대조할 것")

    print("\n결과:", "FAIL — " + "; ".join(failures) if failures else "PASS")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Task 2 완료 후 게이트 실행** (krx_auth 의존 — 순서: Task 2 먼저 커밋, 그 후 실행)

Run: `PYTHONPATH=".venv/lib/python3.11/site-packages:." .venv/bin/python scripts/verify_krx.py`
Expected: `PASS` (rows KOSPI≥1500·KOSDAQ≥1400, 소형주 2종목 포함). `BLOCKED`면 사용자에게 키체인 등록 요청. `FAIL`이면 **여기서 중단하고 사용자와 C안 후퇴 논의**. ③이 미반영이어도 폴백이 있으므로 진행 가능하되 결과를 사용자에게 보고.

- [ ] **Step 5: Commit**

```bash
git add scripts/verify_krx.py
git commit -m "feat: KRX 일괄 API 검증 게이트 스크립트"
```

---

### Task 2: krx_auth — 키체인 → 환경변수 주입

**Files:**
- Create: `modules/krx_auth.py`
- Test: `tests/test_krx_auth.py`

**Interfaces:**
- Produces: `inject_credentials(service: str = "krx-data") -> bool` — 성공 시 os.environ에 KRX_ID/KRX_PW 설정 후 True. 이미 설정돼 있으면 True. 키체인 없음/실패 시 False (예외 전파 없음).

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/test_krx_auth.py
import os
import subprocess
from unittest.mock import patch, MagicMock

import pytest

from modules import krx_auth


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("KRX_ID", raising=False)
    monkeypatch.delenv("KRX_PW", raising=False)


def _mock_security(acct_stdout, pw_stdout):
    def side_effect(cmd, **kwargs):
        m = MagicMock()
        m.returncode = 0
        m.stdout = pw_stdout if "-w" in cmd else acct_stdout
        return m
    return side_effect


def test_inject_success(monkeypatch):
    acct_out = 'keychain: ...\n    "acct"<blob>="myid"\n'
    with patch("subprocess.run", side_effect=_mock_security(acct_out, "mypw\n")):
        assert krx_auth.inject_credentials() is True
    assert os.environ["KRX_ID"] == "myid"
    assert os.environ["KRX_PW"] == "mypw"


def test_inject_already_set(monkeypatch):
    monkeypatch.setenv("KRX_ID", "x")
    monkeypatch.setenv("KRX_PW", "y")
    with patch("subprocess.run") as run:
        assert krx_auth.inject_credentials() is True
        run.assert_not_called()


def test_inject_keychain_missing():
    with patch("subprocess.run",
               side_effect=subprocess.CalledProcessError(44, "security")):
        assert krx_auth.inject_credentials() is False
    assert "KRX_ID" not in os.environ


def test_inject_acct_parse_failure():
    with patch("subprocess.run", side_effect=_mock_security("no acct here", "pw")):
        assert krx_auth.inject_credentials() is False
```

- [ ] **Step 2: 실패 확인**

Run: `PYTHONPATH=".venv/lib/python3.11/site-packages:." /opt/homebrew/bin/python3.11 -m pytest tests/test_krx_auth.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'modules.krx_auth'`

- [ ] **Step 3: 구현**

```python
# modules/krx_auth.py
"""KRX 자격증명 — macOS 키체인(서비스 krx-data)에서 읽어 환경변수로 주입.

pykrx 1.2.5+는 KRX_ID/KRX_PW 환경변수로 data.krx.co.kr에 로그인한다.
비밀번호는 키체인에만 보관 (config.yaml 평문 금지 — 선례: telegram-bot-memtrack).
등록: security add-generic-password -s krx-data -a "<KRX_ID>" -w "<KRX_PW>"
"""
import logging
import os
import re
import subprocess

logger = logging.getLogger(__name__)

SERVICE = "krx-data"


def inject_credentials(service: str = SERVICE) -> bool:
    """키체인에서 KRX ID/PW를 읽어 os.environ에 설정. 실패해도 예외 없이 False."""
    if os.environ.get("KRX_ID") and os.environ.get("KRX_PW"):
        return True
    try:
        attrs = subprocess.run(
            ["security", "find-generic-password", "-s", service],
            capture_output=True, text=True, check=True, timeout=10,
        )
        m = re.search(r'"acct"<blob>="([^"]+)"', attrs.stdout + attrs.stderr)
        if not m:
            logger.warning(f"키체인 {service}: 계정(acct) 파싱 실패")
            return False
        pw = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-w"],
            capture_output=True, text=True, check=True, timeout=10,
        )
        os.environ["KRX_ID"] = m.group(1)
        os.environ["KRX_PW"] = pw.stdout.strip()
        logger.info("KRX 자격증명 주입 완료 (키체인 krx-data)")
        return True
    except Exception as e:
        logger.warning(f"KRX 자격증명 주입 실패({type(e).__name__}) — KRX 수집은 폴백 예정")
        return False
```

- [ ] **Step 4: 통과 확인**

Run: `PYTHONPATH=".venv/lib/python3.11/site-packages:." /opt/homebrew/bin/python3.11 -m pytest tests/test_krx_auth.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit 후 Task 1 Step 4(게이트 실행)로 복귀**

```bash
git add modules/krx_auth.py tests/test_krx_auth.py
git commit -m "feat: KRX 자격증명 키체인 주입 모듈"
```

---

### Task 3: crawler — KRX 전종목 수급 수집 + collect_all 통합

**Files:**
- Modify: `modules/crawler.py` (§5 fchart 블록 뒤, §6 Naver 상세 앞에 신규 블록; 함수는 `crawl_naver_stock_details` 아래에 추가)
- Test: `tests/test_krx_flows.py`

**Interfaces:**
- Produces: `crawl_krx_investor_flows(fromdate: str, todate: str) -> pd.DataFrame` — 컬럼 `[티커, 시장, 1주기관매매, 1주외국인매매]`(억원, float). 빈 응답·예외 시 RuntimeError.
- Produces: `collect_all()` 반환 dict에 `krx_flows`(DataFrame, 실패 시 빈), `flow_source`("krx"|"naver") 추가.
- Consumes: pykrx `stock.get_market_net_purchases_of_equities_by_ticker` (지연 import).

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/test_krx_flows.py
from unittest.mock import patch

import pandas as pd
import pytest

from modules import crawler


def _krx_df(tickers, amounts_won):
    """pykrx 반환 형태 재현: index=티커, 순매수거래대금(원)"""
    return pd.DataFrame(
        {"종목명": [f"종목{t}" for t in tickers],
         "순매수거래대금": amounts_won},
        index=pd.Index(tickers, name="티커"),
    )


def test_crawl_krx_investor_flows_merges_markets_and_investors():
    def fake(fromdate, todate, market, investor):
        assert (fromdate, todate) == ("20260713", "20260717")
        base = {"KOSPI": ["005930"], "KOSDAQ": ["380540"]}[market]
        amt = {"기관합계": [1.5e8], "외국인": [-2.0e8]}[investor]
        return _krx_df(base, amt)

    with patch("pykrx.stock.get_market_net_purchases_of_equities_by_ticker",
               side_effect=fake):
        df = crawler.crawl_krx_investor_flows("20260713", "20260717")

    assert sorted(df.columns) == sorted(["티커", "시장", "1주기관매매", "1주외국인매매"])
    row = df[df["티커"] == "005930"].iloc[0]
    assert row["시장"] == "KOSPI"
    assert row["1주기관매매"] == pytest.approx(1.5)   # 원 → 억원
    assert row["1주외국인매매"] == pytest.approx(-2.0)
    assert set(df["티커"]) == {"005930", "380540"}


def test_crawl_krx_investor_flows_raises_on_empty():
    with patch("pykrx.stock.get_market_net_purchases_of_equities_by_ticker",
               return_value=pd.DataFrame()):
        with pytest.raises(RuntimeError):
            crawler.crawl_krx_investor_flows("20260713", "20260717")
```

- [ ] **Step 2: 실패 확인**

Run: `PYTHONPATH=".venv/lib/python3.11/site-packages:." /opt/homebrew/bin/python3.11 -m pytest tests/test_krx_flows.py -v`
Expected: FAIL — `AttributeError: module 'modules.crawler' has no attribute 'crawl_krx_investor_flows'`

- [ ] **Step 3: 함수 구현** (`crawl_naver_stock_details` 함수 정의 끝(835행 부근) 아래에 추가)

```python
# ─────────────────────────────────────────
# KRX 전종목 투자자 순매수 (일괄, 주 4콜)
# ─────────────────────────────────────────

def crawl_krx_investor_flows(fromdate: str, todate: str) -> pd.DataFrame:
    """KRX [12010] 투자자별 순매수 — 전종목 기관·외국인 주간 순매수(거래대금, 억원).

    시장(KOSPI/KOSDAQ) × 투자자(기관합계/외국인) = 4콜. KRX가 기간 집계를 수행.
    pykrx는 로그인 실패 등에서 예외 없이 빈 DataFrame을 반환하므로 빈 응답도 실패로 취급.
    Returns: DataFrame [티커, 시장, 1주기관매매, 1주외국인매매]
    Raises: RuntimeError (빈 응답), 기타 예외 전파 — 호출자가 폴백 결정.
    """
    from pykrx import stock  # 지연 import — 테스트에서 mock 대상

    per_market = []
    for market in ["KOSPI", "KOSDAQ"]:
        frames = []
        for investor, col in [("기관합계", "1주기관매매"), ("외국인", "1주외국인매매")]:
            df = stock.get_market_net_purchases_of_equities_by_ticker(
                fromdate, todate, market, investor)
            if df is None or df.empty:
                raise RuntimeError(f"KRX 순매수 빈 응답: {market}/{investor} (로그인 실패 가능)")
            frames.append(pd.DataFrame({
                "티커": df.index.astype(str),
                col: (df["순매수거래대금"] / 1e8).round(2).values,
            }))
        merged = frames[0].merge(frames[1], on="티커", how="outer")
        merged["시장"] = market
        per_market.append(merged)

    result = pd.concat(per_market, ignore_index=True)
    logger.info(f"KRX 전종목 수급 수집 완료: {len(result)}개 종목 (거래대금 기준)")
    return result
```

- [ ] **Step 4: collect_all 통합** — `collect_all()`의 §5(fchart) 블록과 §6(Naver 상세) 블록 사이에 삽입. `week_start_dt`(해당 주 월요일)는 두 분기 모두에서 이미 계산돼 있음.

```python
    # ── 5.5 KRX 전종목 투자자 순매수 (기관·외국인, 주 4콜) ──
    monday_str = week_start_dt.strftime("%Y%m%d")
    try:
        result["krx_flows"] = crawl_krx_investor_flows(monday_str, base_str)
        result["flow_source"] = "krx"
    except Exception as e:
        logger.warning(f"KRX 전종목 수급 실패({e}) → Naver 상위200 폴백")
        result["krx_flows"] = pd.DataFrame()
        result["flow_source"] = "naver"
```

- [ ] **Step 5: 폴백 분기 테스트 추가** (`tests/test_krx_flows.py`에 append — collect_all 전체는 네트워크 의존이라 폴백 분기 로직을 동일 형태로 재현해 검증)

```python
def test_collect_all_falls_back_on_krx_failure():
    """KRX 실패 시 flow_source=naver 폴백 분기 (collect_all과 동일 로직)"""
    result = {}
    try:
        with patch("pykrx.stock.get_market_net_purchases_of_equities_by_ticker",
                   side_effect=Exception("LOGOUT")):
            result["krx_flows"] = crawler.crawl_krx_investor_flows("20260713", "20260717")
            result["flow_source"] = "krx"
    except Exception:
        result["krx_flows"] = pd.DataFrame()
        result["flow_source"] = "naver"
    assert result["flow_source"] == "naver"
    assert result["krx_flows"].empty
```

- [ ] **Step 6: 전체 테스트 통과 확인**

Run: `PYTHONPATH=".venv/lib/python3.11/site-packages:." /opt/homebrew/bin/python3.11 -m pytest tests/ -v`
Expected: 전체 passed (기존 24 + krx_auth 4 + krx_flows 3)

- [ ] **Step 7: Commit**

```bash
git add modules/crawler.py tests/test_krx_flows.py
git commit -m "feat: KRX 전종목 투자자 순매수 일괄 수집 + 폴백 분기"
```

---

### Task 4: processor — 수급 컬럼 KRX 대체

**Files:**
- Modify: `modules/processor.py:275-346` (`process()` — KIS investor_ranks 병합 블록 뒤)
- Test: `tests/test_processor_flows.py`

**Interfaces:**
- Consumes: `raw["krx_flows"]` (Task 3 산출 — [티커, 시장, 1주기관매매, 1주외국인매매]), `raw["flow_source"]`.
- Produces: `processed[market]`의 `1주기관매매`/`1주외국인매매`가 KRX 값으로 대체(전종목). `processed["flow_source"]` 추가. 폴백 시 현행(Naver/KIS) 값 유지.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/test_processor_flows.py
import pandas as pd
import pytest

from modules import processor


def _raw(krx_flows, flow_source):
    kospi = pd.DataFrame({
        "티커": ["005930", "000660"],
        "종목명": ["삼성전자", "SK하이닉스"],
        "시가총액(억)": [4000000.0, 1500000.0],
        "시장": ["KOSPI", "KOSPI"],
        # Naver 상세에서 온 기존 수급 (상위 200 표본)
        "1주기관매매": [999.0, None],
        "1주외국인매매": [888.0, None],
    })
    return {
        "kospi": kospi, "kosdaq": pd.DataFrame(),
        "investor_ranks": {}, "market_info": {},
        "base_date": "20260717", "krx_flows": krx_flows, "flow_source": flow_source,
    }


def test_krx_flows_replace_naver_values():
    krx = pd.DataFrame({
        "티커": ["005930", "000660"], "시장": ["KOSPI", "KOSPI"],
        "1주기관매매": [1.5, -3.0], "1주외국인매매": [-2.0, 4.0],
    })
    out = processor.process(_raw(krx, "krx"))
    df = out["kospi"]
    assert list(df.columns).count("1주기관매매") == 1  # 중복 컬럼 없음
    s = df.set_index("티커")
    assert s.loc["005930", "1주기관매매"] == pytest.approx(1.5)   # 999 아님 — KRX로 대체
    assert s.loc["000660", "1주외국인매매"] == pytest.approx(4.0)  # 표본 밖 종목도 채워짐
    assert out["flow_source"] == "krx"
    # 파생 비중도 KRX 값 기준
    assert s.loc["005930", "시가대비_기관매매비중_1주"] == pytest.approx(1.5 / 4000000 * 100)


def test_naver_fallback_keeps_existing_values():
    out = processor.process(_raw(pd.DataFrame(), "naver"))
    s = out["kospi"].set_index("티커")
    assert s.loc["005930", "1주기관매매"] == pytest.approx(999.0)
    assert out["flow_source"] == "naver"
```

- [ ] **Step 2: 실패 확인**

Run: `PYTHONPATH=".venv/lib/python3.11/site-packages:." /opt/homebrew/bin/python3.11 -m pytest tests/test_processor_flows.py -v`
Expected: FAIL (KRX 값 대체 로직 없음 — 999.0이 그대로 남고 flow_source 키 부재)

- [ ] **Step 3: 구현** — `process()`의 KIS 병합 블록(`if "외국인순매수금액" in df.columns: ...` rename까지) 바로 뒤, `add_derived_columns` 호출 앞에 삽입:

```python
        # KRX 전종목 수급으로 대체 (소스 혼용 금지 — 스펙 §4.1)
        krx_flows = raw.get("krx_flows", pd.DataFrame())
        if not krx_flows.empty:
            mkt_flows = krx_flows[krx_flows["시장"] == market.upper()]
            if not mkt_flows.empty:
                drop_cols = [c for c in df.columns if c in ("1주기관매매", "1주외국인매매")]
                if drop_cols:
                    df = df.drop(columns=drop_cols)
                df = df.merge(
                    mkt_flows[["티커", "1주기관매매", "1주외국인매매"]],
                    on="티커", how="left",
                )
```

그리고 `process()` 말미(`processed["period_label"] = ...` 근처)에:

```python
    processed["flow_source"] = raw.get("flow_source", "naver")
```

- [ ] **Step 4: 통과 확인 (전체 스위트)**

Run: `PYTHONPATH=".venv/lib/python3.11/site-packages:." /opt/homebrew/bin/python3.11 -m pytest tests/ -v`
Expected: 전체 passed

- [ ] **Step 5: Commit**

```bash
git add modules/processor.py tests/test_processor_flows.py
git commit -m "feat: 수급 컬럼 KRX 전종목 값으로 대체 (폴백 시 현행 유지)"
```

---

### Task 5: database — flow_source 마이그레이션 + 종목별 주간 이력 조회

**Files:**
- Modify: `modules/database.py` (`_init_db`, `_upsert_stock`, 신규 조회 함수)
- Test: `tests/test_database_flows.py`

**Interfaces:**
- Consumes: `processed["flow_source"]` (Task 4 산출).
- Produces: `weekly_stock.flow_source` 컬럼(TEXT, 기존 행은 'naver' 소급). `Database.get_stock_flow_history(ticker: str, market: str, n_weeks: int = 4) -> pd.DataFrame` — `[week_date, inst_net_1w, foreign_net_1w]` 오름차순.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/test_database_flows.py
import sqlite3

import pandas as pd
import pytest

from modules import database


@pytest.fixture
def db(tmp_path):
    return database.Database(str(tmp_path / "test.db"))


def _processed(flow_source="krx"):
    return {
        "kospi": pd.DataFrame({
            "티커": ["005930"], "1주기관매매": [1.5], "1주외국인매매": [-2.0],
        }),
        "kosdaq": pd.DataFrame(),
        "flow_source": flow_source,
    }


def test_flow_source_column_exists_and_saved(db):
    with db._connect() as conn:
        db._upsert_stock(conn, "20260717", _processed("krx"))
        row = conn.execute(
            "SELECT flow_source FROM weekly_stock WHERE ticker='005930'"
        ).fetchone()
    assert row[0] == "krx"


def test_migration_backfills_naver(tmp_path):
    """flow_source 없는 기존 DB → 마이그레이션 후 기존 행 'naver' 소급"""
    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE weekly_stock (
        week_date TEXT, market TEXT, ticker TEXT,
        inst_net_1w REAL, foreign_net_1w REAL,
        PRIMARY KEY (week_date, market, ticker))""")
    conn.execute("INSERT INTO weekly_stock VALUES ('20260710','KOSPI','005930',10.0,20.0)")
    conn.commit()
    conn.close()

    db = database.Database(path)  # _init_db가 마이그레이션 수행
    with db._connect() as conn:
        row = conn.execute(
            "SELECT flow_source FROM weekly_stock WHERE week_date='20260710'"
        ).fetchone()
    assert row[0] == "naver"


def test_get_stock_flow_history(db):
    with db._connect() as conn:
        for wd, inst in [("20260703", 1.0), ("20260710", 2.0), ("20260717", 3.0)]:
            conn.execute(
                "INSERT INTO weekly_stock (week_date, market, ticker, inst_net_1w, foreign_net_1w, flow_source)"
                " VALUES (?, 'KOSPI', '005930', ?, ?, 'krx')", (wd, inst, -inst))
    hist = db.get_stock_flow_history("005930", "KOSPI", n_weeks=2)
    assert list(hist["week_date"]) == ["20260710", "20260717"]  # 오름차순, 최근 2주
    assert list(hist["inst_net_1w"]) == [2.0, 3.0]
```

- [ ] **Step 2: 실패 확인**

Run: `PYTHONPATH=".venv/lib/python3.11/site-packages:." /opt/homebrew/bin/python3.11 -m pytest tests/test_database_flows.py -v`
Expected: FAIL — `no such column: flow_source` 및 `AttributeError: get_stock_flow_history`

- [ ] **Step 3: 구현**

`DDL_WEEKLY_STOCK`에 컬럼 추가:

```python
DDL_WEEKLY_STOCK = """
CREATE TABLE IF NOT EXISTS weekly_stock (
    week_date      TEXT,
    market         TEXT,
    ticker         TEXT,
    inst_net_1w    REAL,
    foreign_net_1w REAL,
    flow_source    TEXT,
    PRIMARY KEY (week_date, market, ticker)
);
"""
```

`_init_db`에 마이그레이션 추가 (DDL 실행 뒤):

```python
    def _init_db(self):
        with self._connect() as conn:
            conn.execute(DDL_WEEKLY_MARKET)
            conn.execute(DDL_WEEKLY_CAP_WEIGHT)
            conn.execute(DDL_WEEKLY_SECTOR)
            conn.execute(DDL_WEEKLY_STOCK)
            self._migrate_flow_source(conn)
        logger.info(f"DB 초기화 완료: {self.db_path}")

    def _migrate_flow_source(self, conn: sqlite3.Connection):
        """기존 DB에 flow_source 컬럼 추가 + 과거 행은 'naver' 소급 (스펙 §4.6)"""
        cols = [r[1] for r in conn.execute("PRAGMA table_info(weekly_stock)")]
        if "flow_source" not in cols:
            conn.execute("ALTER TABLE weekly_stock ADD COLUMN flow_source TEXT")
            logger.info("weekly_stock: flow_source 컬럼 추가")
        conn.execute("UPDATE weekly_stock SET flow_source='naver' WHERE flow_source IS NULL")
```

`_upsert_stock`의 INSERT 수정 (메서드 서두에 `flow_source = processed.get("flow_source", "naver")` 추가):

```python
                conn.execute(
                    """
                    INSERT OR REPLACE INTO weekly_stock
                    (week_date, market, ticker, inst_net_1w, foreign_net_1w, flow_source)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (week_date, market, ticker, inst_val, fore_val, flow_source),
                )
```

조회 함수 추가 (`get_stock_investor_accumulate` 아래):

```python
    def get_stock_flow_history(self, ticker: str, market: str, n_weeks: int = 4) -> pd.DataFrame:
        """종목 하나의 최근 n_weeks 주간 기관/외국인 순매수 이력 (오름차순).
        Returns: DataFrame [week_date, inst_net_1w, foreign_net_1w]
        """
        with self._connect() as conn:
            df = pd.read_sql_query(
                """
                SELECT week_date, inst_net_1w, foreign_net_1w
                FROM weekly_stock
                WHERE ticker = ? AND market = ?
                ORDER BY week_date DESC LIMIT ?
                """,
                conn, params=(ticker, market, n_weeks),
            )
        return df.sort_values("week_date").reset_index(drop=True)
```

- [ ] **Step 4: 통과 확인 (전체 스위트)**

Run: `PYTHONPATH=".venv/lib/python3.11/site-packages:." /opt/homebrew/bin/python3.11 -m pytest tests/ -v`
Expected: 전체 passed

- [ ] **Step 5: Commit**

```bash
git add modules/database.py tests/test_database_flows.py
git commit -m "feat: weekly_stock flow_source 컬럼 + 종목별 주간 수급 이력 조회"
```

---

### Task 6: watchlist — 파싱 + 섹션 렌더 + 개인용 보고서

**Files:**
- Create: `modules/watchlist.py`
- Test: `tests/test_watchlist.py`

**Interfaces:**
- Consumes: `Database.get_stock_flow_history(ticker, market, n_weeks)` (Task 5), `processed[market]` DataFrame(티커/종목명/시가총액/1주등락률/1주기관매매/1주외국인매매 컬럼), `config["publish"]["watchlist_path"]`, `config["output"]["report_prefix"]`.
- Produces:
  - `load_watchlist(path: str | Path) -> pd.DataFrame` — `[티커, 종목명, 구분]`. 파일 없음/표 없음 → 빈 DataFrame.
  - `build_watchlist_section(wl: pd.DataFrame, processed: dict, db) -> str` — `## 워치리스트 수급`으로 시작하는 markdown.
  - `generate_personal_report(report_path: str, processed: dict, db, config: dict) -> str | None` — 개인용 md 경로. 워치리스트 없음/빈 목록 → None.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/test_watchlist.py
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd

from modules import watchlist

WL_MD = """---
title: 워치리스트
type: topic
---
# 워치리스트

| 티커 | 종목명 | 구분 | 메모 |
|---|---|---|---|
| 005930 | 삼성전자 | 보유 | [[삼성전자]] |
| 380540 | 옵티코어 | 관심 | 로보틱스 테제 |
| 잘못된행 | - | - | 티커 아님 |
"""


def _processed():
    return {
        "kospi": pd.DataFrame({
            "티커": ["005930"], "종목명": ["삼성전자"], "시가총액": [4000000.0],
            "1주등락률": [1.2], "1주기관매매": [1.5], "1주외국인매매": [-2.0],
            "시장": ["KOSPI"],
        }),
        "kosdaq": pd.DataFrame({
            "티커": ["380540"], "종목명": ["옵티코어"], "시가총액": [1500.0],
            "1주등락률": [-3.4], "1주기관매매": [0.3], "1주외국인매매": [0.8],
            "시장": ["KOSDAQ"],
        }),
        "base_date": "20260717",
    }


def _db():
    db = MagicMock()
    db.get_stock_flow_history.return_value = pd.DataFrame({
        "week_date": ["20260710", "20260717"],
        "inst_net_1w": [2.0, 1.5], "foreign_net_1w": [-1.0, -2.0],
    })
    return db


def test_load_watchlist_parses_ticker_rows(tmp_path):
    p = tmp_path / "워치리스트.md"
    p.write_text(WL_MD, encoding="utf-8")
    wl = watchlist.load_watchlist(p)
    assert list(wl["티커"]) == ["005930", "380540"]   # 6자리 숫자만
    assert list(wl["구분"]) == ["보유", "관심"]


def test_load_watchlist_missing_file(tmp_path):
    assert watchlist.load_watchlist(tmp_path / "없음.md").empty


def test_build_section_contains_stocks_and_trend():
    wl = pd.DataFrame({"티커": ["005930", "380540"],
                       "종목명": ["삼성전자", "옵티코어"], "구분": ["보유", "관심"]})
    md = watchlist.build_watchlist_section(wl, _processed(), _db())
    assert md.startswith("## 워치리스트 수급")
    assert "삼성전자" in md and "옵티코어" in md
    assert "+1.5" in md    # 이번 주 기관
    assert "→" in md       # 추이 표기


def test_generate_personal_report(tmp_path):
    wl_path = tmp_path / "워치리스트.md"
    wl_path.write_text(WL_MD, encoding="utf-8")
    report = tmp_path / "주가자금동향_20260717.md"
    report.write_text("---\ntitle: x\n---\n본문", encoding="utf-8")
    config = {"publish": {"watchlist_path": str(wl_path)},
              "output": {"report_prefix": "주가자금동향"}}

    out = watchlist.generate_personal_report(str(report), _processed(), _db(), config)

    assert out is not None and out.endswith("주가자금동향_개인_20260717.md")
    text = Path(out).read_text(encoding="utf-8")
    assert text.startswith("---\ntitle: x")           # 공유용 본문 보존
    assert "## 워치리스트 수급" in text
    shared = report.read_text(encoding="utf-8")
    assert "워치리스트" not in shared                  # 공유용 무오염


def test_generate_personal_report_no_watchlist(tmp_path):
    report = tmp_path / "주가자금동향_20260717.md"
    report.write_text("본문", encoding="utf-8")
    config = {"publish": {"watchlist_path": str(tmp_path / "없음.md")},
              "output": {"report_prefix": "주가자금동향"}}
    assert watchlist.generate_personal_report(str(report), _processed(), _db(), config) is None
```

- [ ] **Step 2: 실패 확인**

Run: `PYTHONPATH=".venv/lib/python3.11/site-packages:." /opt/homebrew/bin/python3.11 -m pytest tests/test_watchlist.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'modules.watchlist'`

- [ ] **Step 3: 구현**

```python
# modules/watchlist.py
"""워치리스트 — 볼트 md 표 파싱 + 개인용 보고서(워치리스트 수급 섹션) 생성.

공유용 보고서와의 문맥 격리(스펙 §4.4): 이 모듈은 LLM을 호출하지 않고,
워치리스트 데이터는 reporter의 프롬프트에 진입하지 않는다.
개인용 = 공유용 본문(바이트 동일) + 결정적 테이블 섹션 append.
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
    dest.write_text(text, encoding="utf-8")
    logger.info(f"개인용 보고서 저장: {dest} (워치리스트 {len(wl)}종목)")
    return str(dest)
```

- [ ] **Step 4: 통과 확인 (전체 스위트)**

Run: `PYTHONPATH=".venv/lib/python3.11/site-packages:." /opt/homebrew/bin/python3.11 -m pytest tests/ -v`
Expected: 전체 passed

- [ ] **Step 5: Commit**

```bash
git add modules/watchlist.py tests/test_watchlist.py
git commit -m "feat: 워치리스트 파싱 + 개인용 보고서 생성 (LLM 미사용 결정적 섹션)"
```

---

### Task 7: publisher — 개인용/공유용 라우팅

**Files:**
- Modify: `modules/publisher.py:59-127` (`publish()`)
- Test: `tests/test_publisher.py` (기존 파일에 테스트 추가 — 기존 테스트 수정 최소화)

**Interfaces:**
- Consumes: `personal_path` (Task 6 산출 경로 또는 None).
- Produces: `publish(config, report_path, *, personal_path=None, to_dm=False, dry_run=False) -> list[str]` — 라우팅: 볼트 반입=개인용(없으면 공유용), 채널 PDF=공유용(현행), DM PDF=개인용(있을 때만, 파일명 `{name}_개인.pdf`, 캡션 `{name} · 워치리스트`).

- [ ] **Step 1: 기존 test_publisher.py의 픽스처/mock 패턴을 읽고 동일 스타일로 실패하는 테스트 추가**

```python
# tests/test_publisher.py 에 추가 (기존 import·픽스처 재사용)

def test_publish_routes_personal_to_vault_and_dm(tmp_path, monkeypatch):
    """개인용 있으면: 볼트=개인용, 채널=공유용, DM=개인용 PDF"""
    shared = tmp_path / "주가자금동향_20260717.md"
    shared.write_text("---\ntitle: x\n---\n공유 본문", encoding="utf-8")
    personal = tmp_path / "주가자금동향_개인_20260717.md"
    personal.write_text("---\ntitle: x\n---\n공유 본문\n## 워치리스트 수급", encoding="utf-8")

    config = _make_config(tmp_path)  # 기존 테스트의 config 헬퍼 재사용 (없으면 동일 구조로 작성)

    ingest_calls = []
    def fake_run(cmd, **kwargs):
        ingest_calls.append(cmd)
        m = MagicMock(); m.returncode = 0; m.stdout = "반입 완료"; m.stderr = ""
        return m

    sent = []
    with patch("modules.publisher.subprocess.run", side_effect=fake_run), \
         patch("modules.publisher.pdf_export.md_to_pdf",
               side_effect=lambda src, dst: dst), \
         patch("modules.publisher.notifier.send_document",
               side_effect=lambda chat, doc, caption=None: sent.append((chat, str(doc), caption))), \
         patch("modules.publisher._krx_trading_days",
               return_value=["20260713", "20260714", "20260715", "20260716", "20260717"]):
        errors = publisher.publish(config, shared, personal_path=str(personal))

    assert errors == []
    # 볼트 반입은 개인용 파일로
    ingest_cmd = ingest_calls[0]
    assert str(personal) in ingest_cmd
    # 채널=공유용 PDF, DM=개인용 PDF
    chats = {c for c, _, _ in sent}
    assert config["publish"]["telegram_channel"] in chats
    assert config["publish"]["notify_chat_id"] in chats
    dm_doc = [d for c, d, _ in sent if c == config["publish"]["notify_chat_id"]][0]
    assert "_개인" in dm_doc


def test_publish_without_personal_keeps_current_behavior(tmp_path):
    """personal_path=None이면 현행과 동일: 볼트=공유용, 채널만 전송"""
    shared = tmp_path / "주가자금동향_20260717.md"
    shared.write_text("---\ntitle: x\n---\n본문", encoding="utf-8")
    config = _make_config(tmp_path)

    ingest_calls, sent = [], []
    def fake_run(cmd, **kwargs):
        ingest_calls.append(cmd)
        m = MagicMock(); m.returncode = 0; m.stdout = "반입 완료"; m.stderr = ""
        return m

    with patch("modules.publisher.subprocess.run", side_effect=fake_run), \
         patch("modules.publisher.pdf_export.md_to_pdf", side_effect=lambda s, d: d), \
         patch("modules.publisher.notifier.send_document",
               side_effect=lambda chat, doc, caption=None: sent.append(chat)), \
         patch("modules.publisher._krx_trading_days",
               return_value=["20260713", "20260714", "20260715", "20260716", "20260717"]):
        errors = publisher.publish(config, shared)

    assert errors == []
    assert str(shared) in ingest_calls[0]
    assert sent == [config["publish"]["telegram_channel"]]
```

주의: `_make_config`·import·mock 경로는 **기존 test_publisher.py의 실제 패턴에 맞춰 조정**할 것 (기존 24건을 깨뜨리지 않는 것이 우선).

- [ ] **Step 2: 실패 확인**

Run: `PYTHONPATH=".venv/lib/python3.11/site-packages:." /opt/homebrew/bin/python3.11 -m pytest tests/test_publisher.py -v`
Expected: 신규 2건 FAIL (`publish() got an unexpected keyword argument 'personal_path'`)

- [ ] **Step 3: 구현** — `publish()` 수정:

시그니처:

```python
def publish(config: dict, report_path, *, personal_path=None, to_dm: bool = False, dry_run: bool = False) -> list[str]:
```

① 볼트 반입 대상 교체 (기존 `--file` 인자 부분):

```python
    ingest_src = Path(personal_path) if personal_path else report_path
    # ... subprocess.run([..., "--file", str(ingest_src), ...])
```

②③ 뒤에 개인용 DM 전송 블록 추가 (`pdf_enabled` 블록 안, 채널 전송 뒤):

```python
            # 개인용 → DM (워치리스트 포함본)
            if personal_path:
                try:
                    pdf_p = pdf_export.md_to_pdf(
                        Path(personal_path),
                        report_path.parent / "pdf" / f"{name}_개인.pdf")
                    notifier.send_document(pub["notify_chat_id"], pdf_p,
                                           caption=f"{name} · 워치리스트")
                except Exception as e:
                    errors.append(f"개인용 DM 전송 실패: {e}")
```

docstring에 라우팅 규칙 1줄 추가: `볼트·DM=개인용(없으면 볼트는 공유용), 채널=공유용`.

- [ ] **Step 4: 통과 확인 (전체 스위트)**

Run: `PYTHONPATH=".venv/lib/python3.11/site-packages:." /opt/homebrew/bin/python3.11 -m pytest tests/ -v`
Expected: 전체 passed (기존 24건 무손상 포함)

- [ ] **Step 5: Commit**

```bash
git add modules/publisher.py tests/test_publisher.py
git commit -m "feat: 발행 라우팅 이원화 — 볼트·DM=개인용, 채널=공유용"
```

---

### Task 8: main.py 배선 + requirements

**Files:**
- Modify: `main.py:102-181` (`run_pipeline`), `requirements.txt`

**Interfaces:**
- Consumes: `krx_auth.inject_credentials()` (Task 2), `raw["flow_source"]` (Task 3), `watchlist.generate_personal_report(...)` (Task 6), `publisher.publish(..., personal_path=...)` (Task 7).

- [ ] **Step 1: run_pipeline 수정** — 네 지점:

[1/6] 직전에 자격증명 주입:

```python
    # KRX 자격증명 주입 (키체인 → 환경변수). 실패해도 계속 — crawler가 폴백.
    from modules import krx_auth
    krx_auth.inject_credentials()

    # [1] 데이터 수집
    logger.info("[1/6] 데이터 수집 (KIS + KRX + 네이버금융)...")
    raw = crawler.collect_all(config, midweek=midweek)

    # KRX 폴백 시 DM 경고 (스펙 §4.1)
    if raw.get("flow_source") == "naver" and config.get("publish"):
        try:
            from modules import notifier
            notifier.send_message(
                config["publish"]["notify_chat_id"],
                "⚠️ WST: KRX 전종목 수급 실패 — Naver 상위200 폴백으로 진행")
        except Exception:
            logger.exception("KRX 폴백 통지 실패 (무시)")
```

[6/6] 뒤에 개인용 생성:

```python
    # [6.5] 개인용 보고서 (공유용 + 워치리스트 섹션 — LLM 미사용)
    personal_path = None
    try:
        from modules import watchlist
        personal_path = watchlist.generate_personal_report(report_path, processed, db, config)
    except Exception:
        logger.exception("개인용 보고서 생성 실패 (공유용만 발행)")
```

발행 호출 수정:

```python
            publisher.publish(config, report_path, personal_path=personal_path)
```

- [ ] **Step 2: requirements.txt 수정**

```
pykrx>=1.2.8
```
(기존 `pykrx>=1.0.35` 행 교체)

- [ ] **Step 3: 전체 스위트 + 문법 확인**

Run: `PYTHONPATH=".venv/lib/python3.11/site-packages:." /opt/homebrew/bin/python3.11 -m pytest tests/ -v && /opt/homebrew/bin/python3.11 -c "import ast; ast.parse(open('main.py').read())"`
Expected: 전체 passed, 문법 OK

- [ ] **Step 4: 통합 리허설 (dry-run — 네트워크·LLM 없이 배선 확인)**

```bash
PYTHONPATH=".venv/lib/python3.11/site-packages:." .venv/bin/python -c "
from unittest.mock import patch, MagicMock
import pandas as pd, main
raw = {'kospi': pd.DataFrame({'티커': ['005930'], '종목명': ['삼성전자'], '시가총액(억)': [4000000.0], '시장': ['KOSPI']}),
       'kosdaq': pd.DataFrame(), 'investor_ranks': {}, 'market_info': {},
       'base_date': '20260717', 'week_date': '20260717', 'week_start': '20260713',
       'biz_days': 5, 'is_midweek': False, 'period_label': '',
       'krx_flows': pd.DataFrame({'티커': ['005930'], '시장': ['KOSPI'], '1주기관매매': [1.5], '1주외국인매매': [-2.0]}),
       'flow_source': 'krx'}
config = main.load_config()
config['publish'] = None  # 발행 차단
with patch('modules.crawler.collect_all', return_value=raw), \
     patch('modules.reporter._call_ai', return_value='(테스트 본문)'), \
     patch('modules.krx_auth.inject_credentials', return_value=True):
    main.run_pipeline(config)
print('배선 리허설 OK')
"
```
Expected: `배선 리허설 OK` — 파이프라인이 [1/6]~[6.5]를 예외 없이 통과 (LLM·발행 mock/차단, data/에 테스트 산출물 생성됨 — worktree라 프로덕션 무영향).

- [ ] **Step 5: Commit**

```bash
git add main.py requirements.txt
git commit -m "feat: 파이프라인 배선 — KRX 자격증명·폴백 통지·개인용 생성·발행 연결"
```

---

### Task 9: 라이브 리허설 (worktree — 채널 전송 없이)

**Files:** 없음 (검증만)

- [ ] **Step 1: 실데이터 미드위크 실행** (midweek는 발행 자동 생략 — 채널 오염 없음. LLM 1회 비용 발생)

```bash
cd ../WST-full-market-flows
PYTHONPATH=".venv/lib/python3.11/site-packages:." .venv/bin/python main.py --midweek
```
Expected: [1/6]에서 `KRX 전종목 수급 수집 완료: ~4,200개 종목` 로그(자격증명 정상 시). 실행 시간이 현행(~14분) 대비 유의미하게 늘지 않음. `data/주가자금동향_YYYYMMDD.md`와 (워치리스트가 있으면) `_개인_` 파일 생성.

주의: 워치리스트 볼트 파일은 Task 10에서 만든다 — 이 시점엔 임시 테스트용을 만들어 config의 `watchlist_path`로 지정해도 되고, 없으면 "생략" 경고 로그가 정상.

- [ ] **Step 2: 산출물 검증**

```bash
PYTHONPATH=".venv/lib/python3.11/site-packages:." .venv/bin/python -c "
import sqlite3, glob
db = sqlite3.connect('history.db')
print(db.execute('SELECT flow_source, COUNT(*) FROM weekly_stock GROUP BY flow_source').fetchall())
print(sorted(glob.glob('data/주가자금동향*'))[-2:])
"
```
Expected: 이번 주 행이 `('krx', ~4200)` (또는 폴백 시 naver ~200). 보고서 수급 TOP 테이블에 종전과 다른(소형주 포함 가능) 종목 등장 가능 — 삼성전자 등 대형주 값을 KRX 화면과 표본 대조.

- [ ] **Step 3: 사용자 보고** — 리허설 결과(행 수·실행 시간·값 대조·보고서 모양)를 사용자에게 보고하고 Task 10(배포) 진행 승인을 받는다.

---

### Task 10: 배포 (게이트: 7/17 금 20시 첫 무인 발행 성공 확인 후)

**Files:**
- 프로덕션 체크아웃 main 병합, `config.yaml`(untracked) 수정, 볼트 파일 2건, 검토 메모 삭제

- [ ] **Step 1: 병합 게이트 확인** — 7/17(금) 20시 발행 자동화 무인 실행이 성공했는지 사용자와 확인 (`pipeline.log`·텔레그램 채널). 미확인 시 여기서 대기.

- [ ] **Step 2: main 병합 + 프로덕션 의존성 갱신**

```bash
cd /Volumes/삼성SSD_2TB/Agents/WeeklyStocksTransaction
git merge --no-ff feature/full-market-flows -m "feat: 전종목 수급 확보 + 보고서 이원화 (스펙 2026-07-16)"
.venv/bin/pip install 'pykrx>=1.2.8'
PYTHONPATH=".venv/lib/python3.11/site-packages:." /opt/homebrew/bin/python3.11 -m pytest tests/ -v
git worktree remove ../WST-full-market-flows
```
Expected: 병합 완료, 전체 테스트 통과.

- [ ] **Step 3: config.yaml에 워치리스트 경로 추가** (untracked — 직접 편집)

```yaml
publish:
  # ... 기존 키 유지 ...
  watchlist_path: "/Users/sjbossa_ai_agent/obsidian/SJbossa's lib/wiki/topics/개인/워치리스트.md"
```

- [ ] **Step 4: 볼트 워치리스트 파일 생성** — `wiki/topics/개인/워치리스트.md`:

```markdown
---
title: 워치리스트
type: topic
created: 2026-07-17
updated: 2026-07-17
tags:
  - 개인
thesis_status: 운영중
---
# 워치리스트

현재 보고 있거나 보유 중인 종목. WST가 매주 금 20시에 이 표의 **티커 열**을 읽어
개인용 수급동향 보고서에 「워치리스트 수급」 섹션을 만든다. 행 추가/삭제 자유.

| 티커 | 종목명 | 구분 | 메모 |
|---|---|---|---|
| 222800 | 심텍 | 관심 | [[심텍]] |

(사용자가 보유·관심 종목으로 채운다 — 위는 형식 예시 1행)
```

생성 후: 볼트 `index.md`에 등재 + `log.md`에 `## [2026-07-17] ingest | 워치리스트 신설` append + `python3 scripts/lint_wiki.py` 통과 확인.

- [ ] **Step 5: 완료 처리 (스펙 §9)**

```bash
rm docs/전종목수급_검토메모_20260713.md
```
볼트: `wiki/topics/개발/자동화 로드맵.md` 「전종목 수급 확보」 `- [x]` + 완료일, `log.md` 마일스톤, `entities/agents/WeeklyStocksTransaction.md`·`AI 에이전트 포트폴리오` 현황 갱신.

- [ ] **Step 6: 첫 실전 확인 예약** — 다음 금요일 20시 실행에서 ①KRX 수집 행 수 ②금요일분 포함 여부(검증 게이트 ③의 최종 확인) ③채널=공유용/DM=개인용 라우팅을 확인하고 사용자에게 보고.

---

## Self-Review 결과 (작성 시 수행)

- 스펙 커버리지: §4.1(Task 2·3·4·8) §4.2(Task 1) §4.3(Task 6·10-4) §4.4(Task 6) §4.5(Task 7) §4.6(Task 5) §5(worktree·게이트: Task 1·10) §6(각 Task 테스트) §7 수용 기준(Task 9·10-6) §9(Task 10-5) — 공백 없음.
- 타입 일관성: `crawl_krx_investor_flows` 반환 컬럼 = processor 소비 컬럼 = 테스트 컬럼 일치. `get_stock_flow_history(ticker, market, n_weeks)` 시그니처가 Task 5 정의·Task 6 mock 호출과 일치. `publish(..., personal_path=...)` Task 7 정의·Task 8 호출 일치.
- 주의점 명시: pykrx 빈 DataFrame 실패 모드(Global Constraints), test_publisher 기존 패턴 준수(Task 7), 검증 게이트 ③의 금요일 한정 확인(Task 1·10-6).
