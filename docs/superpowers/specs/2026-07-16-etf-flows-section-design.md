# ETF 수급 섹션 — 설계 스펙

- 작성: 2026-07-16 (전종목 수급 프로젝트 `2026-07-16-full-market-flows-design.md` 확장)
- 배경: KRX [12010]은 ETF/ETN 미커버 → KRX 전환 후 보고서 TOP 테이블에서 ETF 수급이 사라짐(사용자 관찰). 이를 별도 섹션으로 복원·개선.
- 상태: 설계 승인 완료 (2026-07-16), 구현 계획 확장 대기
- 브랜치: feature/full-market-flows (전종목 수급 브랜치에 함께 구현 — 배포는 동일 금요일 게이트)

## 1. 배경과 목표

전종목 수급은 KRX `get_market_net_purchases_of_equities_by_ticker`([12010])로 수집하는데,
이 API는 **주식만** 커버하고 ETF/ETN은 전혀 반환하지 않는다(실증 확인). 결과적으로
KRX 전환 후 공유용·개인용 보고서의 수급 TOP 테이블에서 ETF 수급(예: 종전 KODEX 코스닥150
+555.6억)이 사라졌다.

**목표**: ETF 기관·외국인 주간 순매수를 별도 수집해 **공유용·개인용 보고서 공통 본문에
「ETF 수급」 섹션**을 추가한다. 데이터는 결정적 테이블로 렌더(LLM 미사용).

## 2. 사전 조사에서 확정된 사실 (2026-07-16 라이브 검증)

- **ETF는 [12010]에 없음**: KOSPI/KOSDAQ/ALL 어느 market에도 ETF 티커(069500·229200·379800) 미포함 확인.
- **pykrx의 ETF 투자자 수급 경로는 `get_etf_trading_volume_and_value` 하나뿐**:
  - `(from, to)` → **ETF 시장 전체** 투자자별 거래실적(기관합계·외국인·개인 등, 순매수거래대금). **1콜.**
    실측(20260706~10): 외국인 +5억, 기관합계 −19,568억, 개인 +19,048억.
  - `(from, to, ticker)` → **개별 ETF** 투자자별. 실측 KODEX 코스닥150(229200): 외국인 +543.3억, 기관 −1,171.1억
    (종전 Naver 기반 +555.6억과 정합 — 정확한 소스 확인).
  - 전종목 티커별 일괄 랭킹 함수는 **없음** → 전체 커버리지는 티커당 1콜 루프.
- **루프 비용**: 20개 ETF 순차 1.1초(55ms/종목) → 1,140개 외삽 **~1~3분**. Naver 순회(48분)와 달리 감당 가능.
  ⚠️ 단, 20콜 프로브의 외삽이므로 전체 1,140콜의 실제 시간·rate-limit은 **라이브 리허설에서 확인 필수**.
- **ETN**: 동일 함수로 티커별 조회는 되나 수급 미미(니치 상품) → **이번 범위 제외**(사용자 결정).
- **Naver main 페이지는 ETF 투자자값 미제공**(None) → Naver 경로 불가.

## 3. 사용자 결정 사항 (2026-07-16 브레인스토밍)

1. **깊이**: 전체 ETF 티커별 TOP + 시장 전체 집계 (큐레이션/집계만 아님).
2. **범위**: ETF만 (ETN 제외).
3. **서술**: 결정적 테이블 (LLM 해설 없음).
4. **TOP 방향**: 순매수 + 순매도 **양방향** (외국인·기관 각각).
5. **반영 대상**: 공유용·개인용 **양쪽** (공통 본문).

## 4. 설계

### §4.1 데이터 수집 — `crawler.crawl_krx_etf_flows(fromdate, todate)`

- 반환: `(etf_flows: pd.DataFrame, etf_market_agg: dict)`
  - `etf_flows` 컬럼: `[티커, 종목명, 1주기관매매, 1주외국인매매]` (억원, 거래대금 기준).
  - `etf_market_agg`: `{"기관": float, "외국인": float, "개인": float}` (억원) — `(from,to)` 집계 1콜에서.
- 개별 ETF 루프: `get_etf_ticker_list(todate)` × `get_etf_trading_volume_and_value(from, to, ticker)`,
  각 df에서 `기관합계`·`외국인`의 `(거래대금, 순매수)` 추출 / 1e8.
- **best-effort**:
  - 티커 개별 예외 → 스킵(그 ETF 제외), 루프 계속.
  - soft time budget(기본 300초) 초과 → 루프 중단, 수집분까지 사용(로그 경고).
  - 시장 집계 콜 실패 → `etf_market_agg=None`(집계 줄 생략).
  - 전체 빈 결과 → 상위 호출자가 섹션 생략.
- 진행 로그: 200종목마다 1줄.
- KRX [12010] 전종목 수급과 **독립** — 한쪽 실패가 다른 쪽에 영향 없음.

### §4.2 수집 배선 — `collect_all`

- `result["etf_flows"]`(DataFrame), `result["etf_market_agg"]`(dict|None) 추가.
- `crawl_krx_etf_flows`를 try/except로 감싸 실패 시 빈 DataFrame·None (파이프라인 계속).
- 기간은 전종목 수급과 동일: `week_start_dt`(월요일)~`base_str`(기준일).

### §4.3 가공 pass-through — `processor.process`

- `processed["etf_flows"]`, `processed["etf_market_agg"]`를 raw에서 그대로 전달(랭킹 계산은 §4.4).

### §4.4 섹션 렌더 — 신규 `modules/etf_section.py`

- `build_etf_section(etf_flows, etf_market_agg) -> str` — 결정적, LLM 무호출.
- 빈 `etf_flows` → `""` 반환(호출자가 섹션 생략).
- 형식:
  ```
  ## ETF 수급

  > 개별 ETF 기관·외국인 순매수(KRX 거래대금 기준, 억원). ETF 시장 전체 집계 + 순매수·순매도 상위.

  **ETF 시장 전체**: 외국인 +5억 · 기관 −19,568억 · 개인 +19,048억

  **외국인 순매수 상위** (TOP5: ETF | 외국인(억) | 기관(억))
  **외국인 순매도 상위** (TOP5)
  **기관 순매수 상위** (TOP5)
  **기관 순매도 상위** (TOP5)

  > 최상위 무브머 자동 카피 (예: 외국인은 KODEX 코스닥150(+543억)에 집중, 기관은 …에서 이탈)
  ```
- TOP5(방향 4개 = 20행) — 기존 주식 TOP20과 균형. 순매도는 값 오름차순(가장 음수).
- `etf_market_agg=None`이면 집계 줄 생략, 표는 유지.

### §4.5 보고서 삽입 — `reporter.generate_report`

- LLM 3파트 본문 + 차트 결합(`report_text`) **뒤에** ETF 섹션을 결정적 삽입:
  `report_text + "\n\n---\n\n" + etf_section` (etf_section 비어있지 않을 때).
- frontmatter는 그대로 최상단 prepend. **공유용 파일에 ETF 섹션 포함.**
- 개인용은 공유용 파일을 읽어 워치리스트를 append(§전종목 스펙 §4.4) →
  **ETF 섹션은 공유용·개인용 양쪽에 자동 포함**, 워치리스트는 개인용에만.
- reporter는 `processed["etf_flows"]`/`["etf_market_agg"]`를 받아 `etf_section.build_etf_section` 호출.

### §4.6 이력 축적 — `database`

- ETF 수급을 `weekly_stock`에 `market="ETF"`, `flow_source="krx"`로 저장.
- 기존 조회(`get_stock_investor_accumulate`·`get_sector_history`·`get_market_flow_from_stock`·
  `get_stock_flow_history`)는 전부 `market` 필터(KOSPI/KOSDAQ 또는 명시 티커)라 **ETF 행은 무해**.
- 이번 iteration은 **이번 주만 표시**(4주 추이 컬럼 없음) — 데이터는 쌓되 표시는 후속.
- `_upsert_stock`이 `processed["etf_flows"]`도 저장하도록 확장(market="ETF").

## 5. 제약 (전종목 스펙 §5 승계)

- 작업은 worktree feature/full-market-flows. 배포는 전종목 수급과 **동일 금요일 게이트**
  (2026-07-17 20시 첫 무인 발행 성공 확인 후 병합).
- 발행 계약 불변: 공유용 파일명 `주가자금동향_YYYYMMDD.md`·frontmatter·차트 `*_기준일.png`.
  ETF 섹션은 공통 본문에 **추가**될 뿐 계약 필드 무변경.
- 테스트 전체 스위트(현재 48건) + 신규 상시 통과.
- 실행 시간: ETF 루프 +1~3분 — 라이브 리허설에서 20시 파이프라인 총시간 재확인.

## 6. 테스트 계획

- `crawl_krx_etf_flows`: mock pykrx — 루프·단위변환·컬럼 형태, 티커 개별 실패 스킵, 빈 결과, 집계 dict.
- `etf_section.build_etf_section`: 4방향 TOP5 랭킹(순매도=오름차순), 집계 줄, 무브머 카피, 빈 입력 → "".
- `reporter` 통합: 공유용에 "## ETF 수급" 존재, 개인용에 "ETF 수급"+"워치리스트 수급" 둘 다, 공유용에 "워치리스트" 없음.
- `database`: weekly_stock에 market="ETF" 저장, 기존 KOSPI/KOSDAQ 쿼리 결과 불변.
- best-effort: collect_all이 ETF 실패 시 빈 결과로 계속.

## 7. 수용 기준

1. 금 20시 무인 실행에서 ETF 수급이 수집되고 공유용·개인용 보고서에 「ETF 수급」 섹션이 나타난다.
2. 섹션은 시장 전체 집계 + 외국인·기관 순매수·순매도 상위 표를 포함한다.
3. ETF 수집 실패 주에도 보고서는 정상 발행되고 ETF 섹션만 생략된다.
4. weekly_stock에 ETF 이력이 market="ETF"로 적재되고 기존 KOSPI/KOSDAQ 집계·추이는 영향받지 않는다.
5. 전체 테스트(48 + 신규) 통과, 발행 계약 불변.

## 8. 범위 외 (후속)

- ETF 4주 추이 표시(데이터는 §4.6으로 축적, 표시는 다음 iteration).
- Excel ETF 시트, ETF 섹터/테마 분류, 유동성 필터(저거래 ETF 스킵으로 루프 단축).
- ETN 수급, ETF에 대한 LLM 해설.

## 9. 완료 시 처리

- 전종목 수급 스펙과 함께 배포(Task 10). 로드맵 「전종목 수급 확보」 항목에 ETF 섹션 완료 병기.
