# WeeklyStocksTransaction 개발 완료 보고서

> 작성일: 2026-03-02
> 상태: **개발 완료 및 검증 완료**
> 대상 독자: 동일하거나 유사한 프로그램을 AI 개발 에이전트(Claude Code 등)와 함께 개발할 개발자

---

## 목차

1. [프로젝트 개요 및 최종 결과](#1-프로젝트-개요-및-최종-결과)
2. [개발계획서 대비 변경사항 총람](#2-개발계획서-대비-변경사항-총람)
3. [최종 시스템 아키텍처](#3-최종-시스템-아키텍처)
4. [모듈별 구현 상세](#4-모듈별-구현-상세)
5. [데이터 소스 전략 변경 상세](#5-데이터-소스-전략-변경-상세)
6. [데이터베이스 스키마](#6-데이터베이스-스키마)
7. [주요 기술 결정 및 이슈 해결](#7-주요-기술-결정-및-이슈-해결)
8. [알려진 제약사항 및 미구현 항목](#8-알려진-제약사항-및-미구현-항목)
9. [AI 에이전트 개발 인사이트](#9-ai-에이전트-개발-인사이트)
10. [향후 개선 방향](#10-향후-개선-방향)
11. [환경 설정 및 실행 방법](#11-환경-설정-및-실행-방법)

---

## 1. 프로젝트 개요 및 최종 결과

### 목적

매주 금요일 20:00 KST, 코스피·코스닥 전 종목 데이터를 자동 수집 → Excel 저장 → 시총 비중 가공 → SQLite 누적 DB 저장 → 시계열 차트 생성 → Claude AI 보고서(Markdown) 자동 작성하는 로컬 Python 프로그램.

### 최종 구현 결과

| 항목 | 계획 | 결과 |
|------|------|------|
| 전체 코드 규모 | - | **3,179행** (9개 파일) |
| 실행 소요 시간 | - | **약 5분** |
| 수집 종목 수 | 전종목 | KOSPI 2,404개 + KOSDAQ 1,820개 = **4,224개** |
| Excel 출력 | 5탭 | **5탭** (코스피/코스닥/증시정보/코스피시총비중/코스닥시총비중) |
| Excel 파일 크기 | - | **643KB** |
| 차트 PNG | 6종 | **6종** 정상 생성 |
| AI 보고서 | 7개 섹션 | **7개 섹션 완전 생성** (약 27,000자) |
| DB 누적 이력 | 52주 | 4개 테이블, 자동 52주 유지 |

### 출력물 파일명 구조

```
data/
├── 주가자금동향_YYYYMMDD.xlsx       # Excel (5탭)
├── 주가자금동향_YYYYMMDD.md         # AI 보고서 (Markdown)
└── charts/
    ├── cap_weight_kospi_YYYYMMDD.png    # 코스피 시총 비중 추이
    ├── cap_weight_kosdaq_YYYYMMDD.png   # 코스닥 시총 비중 추이
    ├── flow_kospi_YYYYMMDD.png          # 코스피 투자자 수급 추이
    ├── flow_kosdaq_YYYYMMDD.png         # 코스닥 투자자 수급 추이
    ├── sector_flow_kospi_YYYYMMDD.png   # 코스피 섹터별 수급
    └── sector_flow_kosdaq_YYYYMMDD.png  # 코스닥 섹터별 수급
```

---

## 2. 개발계획서 대비 변경사항 총람

### 변경 심각도 범례
- 🔴 **대폭 변경**: 설계 전략 자체가 바뀐 항목
- 🟡 **부분 변경**: 의도는 유지되었으나 방법이 달라진 항목
- 🟢 **계획대로**: 계획서 내용 그대로 구현된 항목
- ⚫ **포기/미구현**: 기술적 한계 또는 데이터 소스 부재로 제외된 항목

| 항목 | 계획서 | 실제 구현 | 심각도 | 이유 |
|------|--------|----------|--------|------|
| 전종목 시총/주가 수집 | KIS API | **네이버금융 sise_market_sum** | 🔴 | KIS API 연동 시 불필요한 복잡도, 네이버금융이 전종목 페이지 방식으로 안정적 수집 가능 |
| PER/ROE/외국인비율 수집 | KIS API | **네이버금융 sise_market_sum** | 🔴 | 동일한 이유로 네이버금융에서 일괄 수집 |
| PBR/배당수익률 수집 | KIS API | **네이버금융 main.naver** (상위 200) | 🔴 | KIS 종목별 상세 조회는 종목당 1회 → 4,224번 호출 필요, 사실상 불가 |
| 기관/외국인 수급 수집 | **pykrx** Bulk 조회 | **네이버금융 main.naver** (상위 200) | 🔴 | pykrx API 응답 불안정/오류 → Naver Finance 크롤링으로 대체 |
| 기간별 등락률 수집 | **pykrx** | **fchart API** (비동기) | 🔴 | pykrx 대체 및 4,224개 비동기 수집으로 9초 내 완료 |
| 지수 현재가 | pykrx | **KIS FHPUP02100000** | 🟡 | KIS API는 지수 조회에만 사용 |
| 기관/외국인 상위 30 | KIS FHPST01770000 | **미사용 (API 빈 응답)** | 🔴 | KIS FHPST01770000이 항상 빈 JSON 반환 |
| 재무 데이터(매출/영업이익/ROE) | 네이버금융 | **네이버금융 main.naver** (계획대로) | 🟢 | 계획대로 구현 |
| 섹터(업종) 매핑 | 네이버금융 | **네이버금융 업종 그룹 페이지** | 🟡 | 종목 페이지가 아닌 업종 그룹 페이지(80여개)에서 역방향 매핑 |
| SQLite 누적 DB | 3개 테이블 | **4개 테이블** (weekly_stock 추가) | 🟡 | 다기간 누적 분석을 위해 종목별 주간 수급 테이블 추가 |
| AI 보고서 생성 | 1회 API 호출 | **3회 분할 API 호출** | 🟡 | claude-sonnet-4-6 최대 출력 8,192 토큰으로 7개 섹션 1회 불가 |
| 유동비율 | 네이버금융 수집 | **⚫ 포기** | ⚫ | Naver Finance 재무 테이블에 유동비율 없음 |
| pykrx 라이브러리 | 핵심 의존성 | **⚫ 사용 안 함** | 🔴 | API 불안정으로 전면 대체 |

---

## 3. 최종 시스템 아키텍처

### 파이프라인 플로우

```
[1] crawler.py          네이버금융 + fchart + KIS API
        |
        |  raw dict {kospi, kosdaq, market_info, base_date, ...}
        ↓
[2] processor.py        파생 컬럼 + 시총 구간 비중 + 섹터 집계 + top_stocks
        |
        |  processed dict (enriched DataFrames)
        ↓
[3] database.py         SQLite UPSERT (4개 테이블, max 52주)
        |
        | (DB 이력으로 주간등락 + 다기간 누적 계산 → processed 보강)
        ↓
[4] exporter.py         Excel 5탭 저장
        ↓
[5] visualizer.py       차트 PNG 6종 생성
        ↓
[6] reporter.py         Claude API 3회 호출 → Markdown 보고서
```

### 파일 구조

```
WeeklyStocksTransaction/
├── .venv/                          # Python 3.11 가상환경
├── config.yaml                     # API 키 / 스케줄 / 출력 경로 설정
├── main.py                         # 진입점 (--run-now / 스케줄러)
├── regen_report.py                 # 보고서만 재생성하는 유틸리티
├── history.db                      # SQLite 누적 DB
├── pipeline.log                    # 실행 로그
├── .kis_token_cache.json           # KIS OAuth 토큰 캐시 (자동 생성)
├── modules/
│   ├── __init__.py
│   ├── crawler.py      (975행)     # 데이터 수집
│   ├── processor.py    (342행)     # 데이터 가공
│   ├── database.py     (453행)     # SQLite CRUD
│   ├── exporter.py     (267행)     # Excel 5탭
│   ├── visualizer.py   (255행)     # 차트 6종
│   ├── reporter.py     (463행)     # AI 보고서 (3회 분할 호출)
│   └── scheduler.py    (43행)      # APScheduler
├── docs/
│   ├── 개발계획서.md
│   └── WeeklyStocksTransaction_개발완료보고서.md  ← 이 파일
└── data/
    ├── *.xlsx / *.md
    └── charts/
```

---

## 4. 모듈별 구현 상세

### crawler.py — 데이터 수집 (975행)

#### 클래스 및 주요 함수

| 함수/클래스 | 역할 | 비고 |
|------------|------|------|
| `KISClient` | KIS REST API 클라이언트 | OAuth 토큰 파일 캐시 (`.kis_token_cache.json`) |
| `crawl_naver_market(market_code, max_pages)` | 전종목 시총 순위 수집 | KOSPI 49p(2,404개), KOSDAQ 37p(1,820개) |
| `build_sector_map()` | 업종→섹터 매핑 dict 구성 | 79개 업종 페이지 순회, 4,338개 종목 매핑 |
| `crawl_period_returns_all(tickers)` | fchart 비동기 기간별 등락률 | 4,224개 × 4기간, 동시 20개, **약 9초** |
| `crawl_naver_individual(tickers, delay)` | main.naver 재무+투자자 | 동기, 0.35s 딜레이, 상위 200개만 |
| `collect_kis_market_info(kis)` | KIS 지수 현재가 | KOSPI (iscd=0001), KOSDAQ (iscd=1001) |
| `collect_all(config)` | 전체 수집 파이프라인 | 6단계 순차 실행 |

#### 핵심 데이터 소스 상세

**네이버금융 sise_market_sum (전종목)**
```
URL: https://finance.naver.com/sise/sise_market_sum.nhn
     ?sosok={market_code}&page={page}
market_code: 0=KOSPI, 1=KOSDAQ
수집 필드: 종목명, 티커, 현재가, 등락률, 시가총액, PER, ROE, 외국인비율
```

**네이버금융 업종 그룹 (섹터 매핑)**
```
URL: https://finance.naver.com/sise/sise_group.nhn?type=upjong
  → 업종별 URL 목록 추출
  → 각 업종 페이지에서 종목 티커 수집
  → ticker → sector 역방향 dict 구성
```

**fchart API (기간별 등락률)**
```
URL: http://fchart.stock.naver.com/sise.nhn
     ?symbol={ticker}&timeframe=day&count=135
응답: XML (인코딩: EUC-KR)
파싱: 종가 리스트 추출 → 5일(1주)/22일(1개월)/65일(3개월)/130일(6개월) 전 대비 등락률 계산
동시성: aiohttp + asyncio, semaphore(20)
```

**네이버금융 main.naver (상위 200개 종목 상세)**
```
URL: https://finance.naver.com/item/main.nhn?code={ticker}
수집 필드:
  - PBR: <em id="_pbr"> in <table class="per_table">
  - 배당수익률: per_table의 tr 중 th에 "배당" 포함 → td 값
  - 연간 재무: <table class="tb_type1 tb_num tb_type1_ifrs">
               → 매출액[2], 영업이익[2], ROE[2], 부채비율[2]  (인덱스 2 = 가장 최근 연도)
  - 기관/외국인 수급:
      table.tb_type1 중 summary에 "외국인"+"기관" 포함하는 테이블
      → 최근 5일 행 합산 × 현재가 / 1억 = 주간 순매수(억)
인코딩: UTF-8 (BeautifulSoup에 raw bytes 전달)
딜레이: 0.35초 (rate limiting 방지)
```

**KIS API (지수만)**
```python
# TR_ID: FHPUP02100000 — 국내주식 업종/지수 현재가
params = {
    "fid_cond_mrkt_div_code": "U",
    "fid_input_iscd": "0001"  # KOSPI (KOSDAQ: "1001")
}
수집: 종가, 전일대비, 등락률
주의: iscd=0001 응답이 간헐적으로 이상값 반환 → 조사 필요
```

---

### processor.py — 데이터 가공 (342행)

#### 주요 함수

| 함수 | 역할 |
|------|------|
| `add_derived_columns(df)` | 컬럼명 표준화 + 시가대비 매매비중 + 영업이익률 계산 |
| `compute_cap_weight_groups(df, market)` | 시총 구간별 비중 계산 |
| `aggregate_by_sector(df, market)` | 섹터별 수급 집계 |
| `detect_rotation(sector_history, n_weeks)` | 섹터 로테이션 신호 감지 (DB 이력 기반) |
| `extract_top_stocks(df, n=20)` | 보고서용 상위 종목 5종 추출 |

#### 컬럼명 표준화 (col_map)

```python
col_map = {
    "시가총액(억)":  "시가총액",          # 네이버 원본 → 내부 표준명
    "기관순매수금액": "1주기관매매",        # KIS investor_ranks용 (현재 미사용)
    "외국인순매수금액": "1주외국인매매",    # KIS investor_ranks용 (현재 미사용)
    "등락률_당일":   "1주등락률",          # 당일 등락률로 주간 등락률 대체 (초기)
    "ROE":          "연간_ROE",           # 네이버금융 ROE 컬럼 표준화
}
```

#### 시총 구간 정의

| 구간 | KOSPI | KOSDAQ |
|------|-------|--------|
| Top 10 | ✅ | ✅ |
| 11~20위 | ✅ | ✅ |
| 21~30위 | ✅ | ✅ |
| 31~50위 | ✅ | ✅ |
| 51~100위 | ✅ | ✅ |
| 101~150위 | ✅ | ✅ |
| 151~200위 | ✅ | ❌ |
| 1~200위 합계 | ✅ | ❌ |
| 1~150위 합계 | ❌ | ✅ |

---

### database.py — SQLite CRUD (453행)

→ 상세 스키마는 [6. 데이터베이스 스키마](#6-데이터베이스-스키마) 참조

#### main.py의 DB 활용 흐름

```python
# [3] DB UPSERT (이번 주 데이터 저장)
database.upsert_week(db, week_date, processed, max_weeks=52)

# _enrich_from_db(): DB 이력으로 processed dict 보강
#   - 주간 지수 등락: 직전 주 index_close 대비 계산 (n_weeks >= 2 필요)
#   - 다기간 누적: weekly_stock에서 2주/4주/12주 합산 (n_stock_weeks >= 2/4/12 필요)
processed = _enrich_from_db(db, week_date, processed)
```

---

### exporter.py — Excel 저장 (267행)

#### 탭 구성

| 탭명 | 내용 | 특이사항 |
|------|------|---------|
| 코스피 | 전종목 31개 컬럼 | 타이틀 행(1) + 헤더(2) + 데이터(3~) |
| 코스닥 | 전종목 31개 컬럼 | 동일 |
| 증시정보 | 지수 / 투자자별 순매수 | 외국인/기관: 섹터 집계 합산값(상위 200종목 근사) |
| 코스피 시총비중 | 구간별 비중 테이블 | - |
| 코스닥 시총비중 | 구간별 비중 테이블 | - |

#### STOCK_COLUMNS 정의 방식

```python
# (Excel 헤더, processed DataFrame 컬럼명, 열 너비, 숫자 포맷)
STOCK_COLUMNS = [
    ("시가총액(억)",  "시가총액",  14, "#,##0"),
    ("1주기관매매(억)", "1주기관매매", 16, "#,##0"),
    ...
]
```

> ⚠️ **주의**: Excel 헤더와 src_col(DataFrame 컬럼명)이 다름. `regen_report.py`의 `EXCEL_TO_SRC` dict 참조.

#### 증시정보 탭 수급 데이터 2단계 폴백

```python
# 1순위: KIS investor_ranks (현재 항상 빈 응답)
# 2순위: 섹터 집계 합산 (상위 200종목 기준 근사값)
sec_df = processed.get("kospi_sector", pd.DataFrame())
if not sec_df.empty:
    fore_net = float(sec_df["1주외국인매매합(억)"].sum())
    inst_net = float(sec_df["1주기관매매합(억)"].sum())
```

---

### visualizer.py — 차트 생성 (255행)

#### 차트 6종

| 파일명 | 내용 | X축 | Y축 |
|--------|------|-----|-----|
| cap_weight_kospi | 코스피 시총 비중 추이 | 날짜(주) | 비중(%) |
| cap_weight_kosdaq | 코스닥 시총 비중 추이 | 날짜(주) | 비중(%) |
| flow_kospi | 코스피 투자자 수급 추이 | 날짜(주) | 순매수(억) |
| flow_kosdaq | 코스닥 투자자 수급 추이 | 날짜(주) | 순매수(억) |
| sector_flow_kospi | 코스피 섹터별 수급 (bar) | 순매수(억) | 섹터명 |
| sector_flow_kosdaq | 코스닥 섹터별 수급 (bar) | 순매수(억) | 섹터명 |

```python
# Mac 한글 폰트 설정 (필수)
plt.rcParams["font.family"] = "AppleGothic"
```

> 초기(1주차)에는 1개 데이터 포인트. 주 누적 시 자동으로 추이 라인 형성.

---

### reporter.py — AI 보고서 (463행) ← **계획 대비 가장 큰 변경**

#### 설계 변경: 1회 → 3회 분할 API 호출

**문제**: `claude-sonnet-4-6` 최대 출력 8,192 토큰으로 7개 섹션(약 27,000자) 한 번에 생성 불가
**해결**: 섹션을 3개 파트로 분리, 각각 별도 API 호출 후 합산

| 파트 | 섹션 | 입력 데이터 | 출력 규모 |
|------|------|------------|----------|
| 파트1 | 1(시장요약) + 2(외국인) | 지수, 외국인 TOP20 × 2(금액/비중), 등락률 TOP20 × 2 | ~10,000자 |
| 파트2 | 3(기관) + 4(섹터 로테이션) | 지수, 기관 TOP20 × 4, 섹터 집계, 로테이션 신호 | ~10,000자 |
| 파트3 | 5(시총) + 6(개인) + 7(시사점) | 지수, 시총 구간 비중 × 2 | ~6,600자 |

#### 코드 구조

```python
def generate_report(processed, chart_paths, rotation_data, config):
    ctx = _build_data_context(...)     # 공유 데이터 1회 구성
    parts = [
        ("파트1", _build_prompt_part1(ctx)),
        ("파트2", _build_prompt_part2(ctx)),
        ("파트3", _build_prompt_part3(ctx)),
    ]
    sections = []
    for part_name, prompt in parts:
        text = _call_ai(prompt, provider, model, api_key, max_tokens)
        sections.append(text.strip())
    report_text = "\n\n---\n\n".join(sections)
```

#### API 비용 (실측 기준)

| 항목 | 값 |
|------|---|
| 파트당 입력 토큰 | ~5,000~8,000 |
| 파트당 출력 토큰 | ~4,000~5,000 |
| 1회 실행 (3파트) 비용 | ~$0.20~0.30 |
| 연간 52회 예상 비용 | ~$10~15 |

---

### regen_report.py — 보고서 재생성 유틸리티 (191행) ← **신규 추가**

전체 파이프라인(약 5분) 재실행 없이 기존 Excel + DB 데이터로 보고서만 재생성.

```bash
.venv/bin/python regen_report.py           # 최신 week_date 자동 탐지
.venv/bin/python regen_report.py 20260227  # 특정 날짜 지정
```

**동작**: Excel 헤더를 `EXCEL_TO_SRC` dict로 변환 → `extract_top_stocks()` 재계산 → DB에서 sector/market_info 재구성 → `reporter.generate_report()` 호출

---

## 5. 데이터 소스 전략 변경 상세

### 계획서 vs. 최종 구현 비교

```
[계획서]
티커/시총/거래대금 ──── KIS API
PER/PBR/배당수익률 ──── KIS API
기관/외국인 순매수 ──── pykrx Bulk 조회 (일별 합산)
등락률 (기간별) ─────── pykrx
섹터/재무 ──────────── 네이버금융

[최종 구현]
티커/시총/주가/PER/ROE ─ 네이버금융 sise_market_sum ← 🔴변경
PBR/배당/재무/투자자 ──── 네이버금융 main.naver (상위 200) ← 🔴변경
등락률 (기간별) ─────── fchart API (비동기) ← 🔴변경
섹터 매핑 ──────────── 네이버금융 업종 그룹 페이지 ← 🟡변경
지수 현재가 ─────────── KIS FHPUP02100000 ← 🟡변경 (범위 축소)
기관/외국인 순매수 TOP30 ─ KIS FHPST01770000 → ⚫미사용 (빈 응답)
pykrx ────────────────── ⚫미사용 (API 불안정)
```

### pykrx 미사용 경위

개발 과정에서 pykrx의 기관/외국인 순매수 데이터 조회 API가 불안정하여 신뢰할 수 있는 응답을 받지 못했다. 이에 네이버금융 `main.naver` 페이지를 크롤링하여 최근 5일 투자자별 거래량 합산 방식으로 대체하였다. 결과적으로 pykrx는 최종 구현에서 전혀 사용되지 않는다.

### 네이버금융 투자자 수급 수집 방식

```python
# main.naver의 투자자 테이블에서 최근 5일 합산
# table.tb_type1 중 summary에 "외국인"+"기관" 포함하는 테이블
# 각 행(일자): 기관수량, 외국인수량 → 5행 합산 × 현재가 / 1억 = 주간 순매수(억)
```

> ⚠️ **제약**: 이 방식은 시가총액 상위 200개 종목에만 적용됨. 나머지 종목의 투자자 수급 데이터는 없음.

### fchart API 발견 및 도입

pykrx 대체를 검토하던 중 네이버금융 차트 데이터를 제공하는 fchart API를 발견. 비공식 API이나 안정적으로 동작. 비동기(aiohttp) 방식으로 4,224개 종목을 약 9초 만에 처리.

```
응답 형식: XML
인코딩: EUC-KR (주의: main.naver는 UTF-8)
데이터: 일자별 종가 리스트 (최근 135일치)
파싱: 종가 리스트에서 n일 전 인덱스의 값을 참조해 등락률 계산
```

---

## 6. 데이터베이스 스키마

계획서의 3개 테이블에서 `weekly_stock` 테이블이 추가되어 최종적으로 **4개 테이블**.

```sql
-- 주별 지수/시장 수급 정보
CREATE TABLE IF NOT EXISTS weekly_market (
    week_date             TEXT,
    market                TEXT,    -- KOSPI / KOSDAQ
    index_close           REAL,
    weekly_pt_change      REAL,    -- 주간 포인트 변동 (2주차부터 산출)
    weekly_pct_change     REAL,    -- 주간 등락률(%) (2주차부터 산출)
    daily_pt_change       REAL,
    daily_pct_change      REAL,
    weekly_foreign_net    REAL,    -- 외국인 순매수 합(억) — 섹터 집계 근사값
    weekly_inst_net       REAL,    -- 기관 순매수 합(억) — 섹터 집계 근사값
    weekly_individual_net REAL,    -- 개인 순매수 (현재 항상 NULL)
    PRIMARY KEY (week_date, market)
);

-- 주별 시총 구간 비중 (차트용)
CREATE TABLE IF NOT EXISTS weekly_cap_weight (
    week_date   TEXT,
    market      TEXT,
    group_name  TEXT,    -- "Top 10", "11~20위", ...
    stock_count INTEGER,
    group_cap   REAL,
    total_cap   REAL,
    weight_pct  REAL,
    PRIMARY KEY (week_date, market, group_name)
);

-- 주별 섹터별 수급 집계 (차트 + 로테이션 분석용)
CREATE TABLE IF NOT EXISTS weekly_sector (
    week_date           TEXT,
    market              TEXT,
    sector              TEXT,              -- 네이버금융 업종명 그대로
    stock_count         INTEGER,
    total_market_cap    REAL,
    foreign_net         REAL,
    inst_net            REAL,
    foreign_cap_ratio   REAL,
    inst_cap_ratio      REAL,
    avg_return_1w       REAL,
    PRIMARY KEY (week_date, market, sector)
);

-- 종목별 주간 투자자 수급 (다기간 누적 계산용) ← 계획 대비 추가
CREATE TABLE IF NOT EXISTS weekly_stock (
    week_date      TEXT,
    market         TEXT,
    ticker         TEXT,
    inst_net_1w    REAL,    -- 기관 1주 순매수(억)
    foreign_net_1w REAL,    -- 외국인 1주 순매수(억)
    PRIMARY KEY (week_date, market, ticker)
);
```

### 다기간 누적 분석 로직

`weekly_stock` 이력이 쌓이면 자동으로 다기간 컬럼이 활성화됨:

| n_stock_weeks | 활성화 컬럼 |
|---------------|------------|
| ≥ 2 | 2주기관매매, 2주외국인매매 |
| ≥ 4 | 1개월기관매매, 1개월외국인매매 |
| ≥ 12 | 3개월기관매매, 3개월외국인매매 |

```python
# main.py _enrich_from_db()에서 자동 계산
acc = db.get_stock_investor_accumulate(market, n_weeks)
df = df.merge(acc[["티커", inst_col, fore_col]], on="티커", how="left")
```

---

## 7. 주요 기술 결정 및 이슈 해결

### Mac 잠자기(Sleep) 시 예약 실행 누락 → launchd 전환

**문제**: `python main.py` 스케줄러 모드(APScheduler)는 Python 프로세스가 살아있어야 동작한다. Mac이 잠자기 상태로 전환되면 프로세스가 일시정지되어 예약 시각에 실행되지 않을 수 있다.

**`misfire_grace_time=3600` 부분 보호**: 깨어날 때 누락된 작업을 검사해, 예약 시각으로부터 **1시간 이내**라면 즉시 실행한다. 1시간 초과 시 실행이 완전히 누락된다.

| 시나리오 | APScheduler | launchd |
|---|---|---|
| 예약 시각에 Mac 켜져 있음 | ✅ | ✅ |
| 예약 시각 ±1시간 내 잠자기 | ✅ 깨어나면 즉시 | ✅ |
| 예약 시각 1시간 이후 깨어남 | ❌ 누락 | ✅ |
| 재부팅 후 자동 등록 | ❌ 수동 재시작 | ✅ 자동 |

**해결**: macOS `launchd`(OS 레벨 스케줄러)로 전환. Python 프로세스 없이도 예약 시각에 Mac을 직접 깨워 `--run-now`를 실행한다.

**외장 SSD 환경에서의 추가 이슈 및 해결 (2026-03-20 검증)**:

프로젝트가 외장 SSD에 위치할 경우, macOS TCC(Transparency, Consent, and Control) 정책으로 인해 추가적인 문제가 발생했다:

1. **"service inactive" (종료코드 78)**: launchd 도메인에서 서비스가 비활성 상태. `launchctl enable`로 해결.
2. **"Operation not permitted" (종료코드 126)**: `/bin/bash`에 전체 디스크 접근 권한(FDA) 미부여. 시스템 설정에서 FDA 추가로 해결.
3. **"realpath: .venv/bin/: Operation not permitted"**: `.venv/bin/python` 심볼릭 링크를 Python이 `realpath()`로 해석할 때 외장 SSD 접근 차단. **homebrew Python(`/opt/homebrew/bin/python3.11`)을 직접 사용하고 `PYTHONPATH`로 venv site-packages 지정**하여 해결.
4. **Python 바이너리 FDA 필요**: bash에만 FDA를 부여하면 bash는 접근 가능하지만, `exec`으로 전환된 Python 프로세스는 별도 TCC 프로필 적용. Python3.11에도 FDA 추가 필요.
5. **로그 파일 경로**: 외장 SSD에 로그 생성이 차단될 수 있어 `/tmp/`에 기록하도록 변경.

**최종 plist 구조**: `/bin/bash -c`로 인라인 명령 실행, `PYTHONPATH` 설정 + homebrew Python 직접 호출 + `/tmp/` 로그.

```bash
# plist 위치: ~/Library/LaunchAgents/com.weeklystocks.pipeline.plist
# 등록
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.weeklystocks.pipeline.plist
# 상태 확인 (- 0 com.weeklystocks.pipeline = 정상 대기)
launchctl list | grep weeklystocks
# 테스트 실행
launchctl kickstart -kp gui/$(id -u)/com.weeklystocks.pipeline
# 로그 확인
tail -f /tmp/weeklystocks_pipeline.log
```

**FDA 필요 대상** (시스템 설정 → 개인 정보 보호 및 보안 → 전체 디스크 접근 권한):
- `/bin/bash` — launchd 에이전트가 외장 SSD 접근
- `/opt/homebrew/bin/python3.11` — Python이 외장 SSD의 스크립트/패키지 접근

**launchd 전환 후 변경사항**:
- `python main.py`(스케줄러 모드) 상시 실행 불필요 — 터미널을 닫아도 됨
- `python main.py --run-now` 수동 실행과 launchd 자동 실행은 완전히 독립적
- 재부팅 시 `LaunchAgents` 폴더 plist 자동 로드 → 별도 조치 불필요
- launchd 실행 시 로그는 `/tmp/weeklystocks_pipeline.log`에 기록

### KIS OAuth 토큰 관리

KIS API는 접근 토큰이 1분당 1회 갱신 제한이 있다. `.kis_token_cache.json`에 토큰을 파일 캐시로 저장하여 반복 호출 시 재사용한다.

```python
# .kis_token_cache.json 구조
{"access_token": "...", "expires_at": "2026-03-01T23:00:00"}
```

### 네이버금융 크롤링 rate limiting

- `sise_market_sum`: 동기 순차 페이지 크롤링 (딜레이 없음 — 사이트 부하 낮음)
- `main.naver`: 동기, **0.35초 딜레이** 필수 (빠르면 IP 차단 위험)
- `fchart`: 비동기, `asyncio.Semaphore(20)` 동시 20개 제한

### fchart EUC-KR 인코딩 처리

```python
# XML 응답이 EUC-KR 인코딩
content = await response.read()  # raw bytes
tree = ET.fromstring(content.decode("euc-kr"))
```

> main.naver는 UTF-8이므로 BeautifulSoup에 raw bytes를 직접 전달 (encoding 파라미터 불필요).

### max_tokens 설정과 보고서 분할

- `claude-sonnet-4-6` 최대 출력: **8,192 토큰**
- 전체 7개 섹션 보고서: **약 27,000자 (≈ 12,000~15,000 토큰)**
- 해결: 3파트 분할, 각 파트 독립적 프롬프트 + 지침("## 3. 으로 시작") 명시
- `config.yaml`의 `max_tokens: 8192`로 조절 가능

### 섹터 매핑의 역방향 구성

계획서는 종목 페이지에서 섹터를 수집하려 했으나, 실제로는 업종 그룹 페이지 순회 방식이 더 효율적:

```
1. https://finance.naver.com/sise/sise_group.nhn?type=upjong 에서 업종 목록 추출
2. 각 업종 URL 순회 → 해당 업종에 속한 종목 티커 수집
3. ticker → sector 역방향 dict 구성
결과: 4,338개 종목 매핑, 79개 업종
```

### KIS 상위종목 API 대체 처리

`FHPST01770000` (기관/외국인 순매수 상위 30)이 항상 빈 JSON 반환. 이로 인해 `investor_ranks` dict는 항상 비어 있으며, 수급 TOP30은 `main.naver`에서 수집한 상위 200 종목 데이터를 정렬해 대체.

---

## 8. 알려진 제약사항 및 미구현 항목

| 항목 | 상태 | 설명 |
|------|------|------|
| **유동비율** | ⚫ 포기 | Naver Finance 재무 테이블에 유동비율 없음 |
| **KIS FHPST01770000** | 🔴 비작동 | 항상 빈 응답 반환. 기관/외국인 시장 전체 데이터 대안 없음 |
| **개인 주간 순매수** | ⚫ 데이터 없음 | Naver Finance에서 수집 불가. DB와 Excel에서 항상 NULL |
| **주간 지수 등락** | ⏳ 2주차부터 | DB 1주차에는 직전 주 없음 → 자동으로 2주차부터 산출 |
| **2주/1개월/3개월 투자자 누적** | ⏳ 2/4/12주차부터 | weekly_stock 누적 필요 (자동 활성화) |
| **KOSPI 지수 KIS iscd=0001** | 🟡 간헐적 이상 | 일부 경우 비정상적 값 반환. 모니터링 필요 |
| **전종목 투자자 수급** | 🟡 상위 200만 | main.naver 크롤링 대상 상위 200종목에 한해서만 기관/외국인 수급 보유 |
| **fchart API 공식성** | 🟡 비공식 | 네이버 비공식 내부 API. 장기적으로 URL 변경 가능성 |

---

## 9. AI 에이전트 개발 인사이트

이 프로젝트는 Claude Code (AI 개발 에이전트)와 협업하여 개발되었다. 유사한 프로젝트를 AI 에이전트와 함께 개발할 때 참고할 핵심 인사이트를 정리한다.

### 9-1. 계획 단계에서 핵심 API를 반드시 사전 검증하라

**상황**: 개발계획서에서 pykrx를 기관/외국인 수급 데이터의 핵심 소스로 지정했으나, 실제 구현 과정에서 API 불안정으로 전면 대체가 필요했다.

**교훈**:
- 데이터 파이프라인 설계 전 **반드시 핵심 API를 직접 호출해보고 응답을 확인**한다.
- 계획 단계에서 검증되지 않은 API를 "핵심 소스"로 지정하면 개발 중반에 대규모 재설계가 필요해진다.
- **대체 소스를 2개 이상 미리 파악**해두면 전략 변경 시 손실을 줄일 수 있다.

### 9-2. LLM 출력 토큰 한계를 설계에 반드시 반영하라

**상황**: 보고서 7개 섹션이 단일 API 호출 최대 토큰(8,192)을 초과해 보고서가 중간에 잘림.

**교훈**:
- LLM이 생성할 텍스트 분량을 미리 추정해서 **출력 토큰 한계 이내인지 확인**한다.
- 초과 시 **자연스러운 분할 경계(섹션, 파트 등)에서 나눠 호출**하고 합산한다.
- 분할 호출 시 각 프롬프트에 **어느 섹션부터 시작하는지, 이전 내용이 이미 있다는 것**을 명시한다.

```
✅ 좋은 예: "## 3. 으로 시작하세요. (# 제목 헤더와 섹션 1~2는 이미 작성됨)"
❌ 나쁜 예: 단순히 "섹션 3~4만 작성하세요" (LLM이 # 헤더부터 다시 시작할 수 있음)
```

### 9-3. 크롤링 vs. 공식 API: 무료 크롤링이 더 풍부한 경우가 있다

**상황**: 유료/공식 API(KIS, pykrx)보다 네이버금융 무료 크롤링이 더 많은 필드를 안정적으로 제공.

**교훈**:
- 공식 API가 항상 최선은 아니다. **데이터 풍부성, 안정성, 접근 용이성**을 종합 비교하라.
- 비공식 크롤링은 **URL 변경/차단 리스크**가 있으므로, 크롤링 코드를 모듈화하고 실패 시 로깅을 철저히 한다.
- **rate limiting**은 크롤링의 핵심 제약. 딜레이 없이 고속 크롤링하면 IP 차단된다.

### 9-4. 데이터 이력 누적 설계는 처음부터 충분히 유연하게

**상황**: 초기 DB 스키마 3개 테이블로 설계했으나, 다기간 투자자 누적 분석을 위해 `weekly_stock` 테이블을 추가해야 했다.

**교훈**:
- 누적/이력 분석이 필요한 데이터는 **처음부터 종목 레벨 이력 테이블을 고려**하라.
- SQLite의 `CREATE TABLE IF NOT EXISTS`와 `INSERT OR REPLACE`를 활용하면 스키마 진화가 비교적 쉽다.
- DB 스키마를 나중에 변경하면 기존 이력 데이터 마이그레이션이 필요해진다.

### 9-5. 인코딩 이슈: 한국어 웹 사이트의 혼재 인코딩

**상황**: 네이버금융 내에서도 페이지별로 UTF-8 / EUC-KR 인코딩이 혼재.

**교훈**:
- 한국어 웹 사이트 크롤링 시 **페이지별 인코딩을 개별 확인**한다.
- BeautifulSoup 사용 시 `from_encoding` 파라미터 대신 **raw bytes를 직접 전달**하는 방식이 더 안전한 경우가 있다.
- fchart(EUC-KR) vs. main.naver(UTF-8) 같은 혼재 상황에서는 별도 디코딩 처리 필수.

### 9-6. 폴백(Fallback) 전략을 처음부터 설계에 포함하라

**상황**: KIS investor_ranks가 비어 있을 때 증시정보 탭의 외국인/기관 순매수가 모두 NULL.

**교훈**:
- 각 데이터 필드에 대해 **"1차 소스가 실패하면 어디서 가져올 것인가"를 미리 설계**한다.
- 이 프로젝트에서는 `kospi_sector` 집계 합산이 1차 소스(KIS) 실패 시 폴백으로 사용됨.
- AI 에이전트에게 폴백 로직을 구현할 때 **"2순위 폴백"이라고 명시적으로 언급**하면 더 정확히 구현된다.

### 9-7. AI 에이전트와의 협업 팁

- **프롬프트보다 코드가 명확하다**: AI에게 "이런 기능 추가해줘"보다 구체적 입출력 예시를 제공하면 오류가 줄어든다.
- **Context 압축 주의**: 긴 세션에서 이전 결정사항이 컨텍스트 압축으로 사라질 수 있다. MEMORY.md에 핵심 결정을 기록해두면 연속성이 유지된다.
- **단계별 검증**: 모듈 하나를 완성할 때마다 실제 실행해보고 다음 단계로 넘어간다. 한꺼번에 구현 후 디버깅하면 원인 찾기가 어렵다.
- **이미 읽은 파일 재활용**: AI 에이전트는 파일을 다시 읽을 때 최신 상태를 반영한다. "앞에서 본 내용 기억하지?"가 아니라 필요 시 다시 읽도록 유도한다.

---

## 10. 향후 개선 방향

| 항목 | 우선순위 | 설명 |
|------|---------|------|
| **전종목 투자자 수급 확보** | ⬆️ 높음 | 현재 상위 200종목만 수급 데이터 있음. 전종목 확장 방법 탐색 필요 |
| **KIS FHPST01770000 재검토** | ⬆️ 높음 | API 정상화 여부 주기적 확인. 정상화 시 기관/외국인 TOP30 데이터 활용 가능 |
| **개인 순매수 데이터 소스** | 🔵 중간 | pykrx 또는 다른 소스에서 시장 전체 개인 순매수 수집 방법 탐색 |
| **KOSPI 지수 이상값 조사** | 🔵 중간 | KIS iscd=0001 간헐적 이상값 원인 분석 |
| **fchart API 공식화/대안** | 🔵 중간 | fchart 비공식 API 대체 가능한 공식 소스 탐색 |
| **보고서 다중 언어 지원** | 🟢 낮음 | 영문 보고서 생성 옵션 추가 |
| **텔레그램/이메일 발송** | 🟢 낮음 | 보고서 생성 완료 후 알림 자동화 |

---

## 11. 환경 설정 및 실행 방법

### 전제 조건

- Python 3.11+
- macOS (한글 폰트 `AppleGothic` 필요)
- KIS 개인 APP Key/Secret (한국투자증권 Developers 발급)
- Anthropic API Key

### 설치

```bash
cd WeeklyStocksTransaction
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### config.yaml 설정

```yaml
kis:
  app_key: "YOUR_KIS_APP_KEY"
  app_secret: "YOUR_KIS_APP_SECRET"
  account_no: "XXXXXXXX-01"        # 계좌번호 (형식: 8자리-01)

ai:
  provider: "anthropic"            # anthropic / openai / google
  model: "claude-sonnet-4-6"
  api_key: "sk-ant-..."
  max_tokens: 8192                 # claude-sonnet-4-6 최대 출력 토큰

schedule:
  day_of_week: "fri"               # 매주 금요일
  hour: 20
  minute: 0
  timezone: "Asia/Seoul"

output:
  excel_dir: "./data"
  report_dir: "./data"
  chart_dir: "./data/charts"
  history_db: "./history.db"
  excel_prefix: "주가자금동향"      # 파일명: 주가자금동향_YYYYMMDD.xlsx
  report_prefix: "주가자금동향"
  max_weeks: 52
```

### 실행

```bash
# 디렉토리 이동 (중요: 상대 경로 기준 실행)
cd /path/to/WeeklyStocksTransaction

# 즉시 실행 (전체 파이프라인, 약 5분)
.venv/bin/python main.py --run-now

# 보고서만 재생성 (기존 Excel + DB 이용, 약 5~8분)
.venv/bin/python regen_report.py
.venv/bin/python regen_report.py 20260227  # 특정 날짜

# 스케줄러 모드 (레거시, 비권장 — Mac sleep 시 누락 위험)
.venv/bin/python main.py
```

### launchd 자동 실행 설정 (권장)

Mac sleep 시에도 안정적으로 예약 실행하려면 launchd를 사용한다. 상세 내용은 [섹션 7 — Mac 잠자기 이슈](#mac-잠자기sleep-시-예약-실행-누락--launchd-전환) 및 README.md 참조.

```bash
# plist 위치: ~/Library/LaunchAgents/com.weeklystocks.pipeline.plist

# 등록 (최초 1회)
launchctl load ~/Library/LaunchAgents/com.weeklystocks.pipeline.plist

# 상태 확인
launchctl list | grep weeklystocks

# 즉시 테스트 실행
launchctl start com.weeklystocks.pipeline

# launchd 관련 로그
tail -f /path/to/WeeklyStocksTransaction/pipeline.log
tail -f /path/to/WeeklyStocksTransaction/pipeline_err.log
```

### 실행 로그 확인

```bash
tail -f pipeline.log
```

### DB 상태 확인

```bash
.venv/bin/python -c "
import sqlite3
conn = sqlite3.connect('history.db')
for t in ['weekly_market','weekly_cap_weight','weekly_sector','weekly_stock']:
    n = conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
    d = conn.execute(f'SELECT COUNT(DISTINCT week_date) FROM {t}').fetchone()[0]
    print(f'{t}: {n}행, {d}주')
conn.close()
"
```

---

*본 보고서는 2026-03-02 기준 WeeklyStocksTransaction v1.0 개발 완료 시점에 최초 작성되었습니다.*
*2026-03-20 업데이트: Mac 잠자기 시 예약 실행 누락 이슈 분석 및 launchd 전환. 외장 SSD 환경에서의 macOS TCC 정책 이슈 (FDA, venv realpath, 로그 경로) 해결 과정 추가 (섹션 7, 섹션 11).*
