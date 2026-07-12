# WeeklyStocksTransaction

매주 금요일 20:00 KST, 코스피·코스닥 전 종목 데이터를 자동 수집하여 Excel 보고서, 시계열 차트, Claude AI 분석 보고서를 생성하는 로컬 Python 자동화 프로그램.

---

## 목차

1. [프로젝트 소개](#1-프로젝트-소개)
2. [설치 방법](#2-설치-방법)
3. [환경변수 설정](#3-환경변수-설정)
4. [사용법](#4-사용법)
5. [launchd 설정 (Mac 자동 실행)](#5-launchd-설정-mac-자동-실행)
6. [출력 결과물](#6-출력-결과물)
7. [라이선스](#7-라이선스)

---

## 1. 프로젝트 소개

### 주요 기능

- **전종목 수집**: KOSPI 2,404개 + KOSDAQ 1,820개 시총/주가/PER/ROE/외국인비율
- **기간별 등락률**: 1주/1개월/3개월/6개월 (비동기 수집, 약 9초)
- **투자자 수급**: 기관·외국인 순매수(상위 200종목), PBR, 배당수익률, 연간 재무
- **DB 누적 이력**: SQLite에 52주치 이력 자동 관리 → 다기간 수급 누적 컬럼 자동 활성화
- **Excel 5탭 출력**: 코스피 / 코스닥 / 증시정보 / 코스피시총비중 / 코스닥시총비중
- **차트 6종 PNG**: 시총비중 추이 × 2, 투자자 수급 추이 × 2, 섹터별 수급 × 2
- **AI 보고서**: Claude API로 7개 섹션 Markdown 보고서 자동 생성 (~27,000자)

### 기술 스택

| 항목 | 내용 |
|------|------|
| Python | 3.11+ |
| 데이터 수집 | 네이버금융 크롤링, fchart 비공식 API, KIS REST API |
| 비동기 | aiohttp + asyncio (fchart 4,224개 종목 동시 수집) |
| DB | SQLite (4개 테이블, 52주 이력) |
| AI | Anthropic Claude API (claude-sonnet-4-6) |
| 스케줄링 | macOS launchd (권장) 또는 APScheduler |

### 파이프라인 구조

```
[1] crawler.py      네이버금융 + fchart + KIS API → raw 데이터
[2] processor.py    파생 컬럼 + 시총비중 + 섹터 집계
[3] database.py     SQLite UPSERT → DB 이력 기반 다기간 누적 보강
[4] exporter.py     Excel 5탭 저장
[5] visualizer.py   차트 PNG 6종
[6] reporter.py     Claude API 3회 분할 호출 → Markdown 보고서
```

---

## 2. 설치 방법

### 전제 조건

- macOS (AppleGothic 한글 폰트 필요)
- Python 3.11+
- KIS (한국투자증권) Developers API Key
- Anthropic API Key

### 설치

```bash
git clone <repo-url>
cd WeeklyStocksTransaction

# 가상환경 생성
python3.11 -m venv .venv
source .venv/bin/activate

# 의존성 설치
pip install -r requirements.txt
```

---

## 3. 환경변수 설정

`config.yaml` 파일에 API 키와 설정을 입력합니다. (`.gitignore`에 등록되어 있어 git에 커밋되지 않습니다.)

```yaml
kis:
  app_key: "YOUR_KIS_APP_KEY"          # 한국투자증권 Developers에서 발급
  app_secret: "YOUR_KIS_APP_SECRET"
  account_no: "XXXXXXXX-01"            # 계좌번호 (형식: 8자리-01)

ai:
  provider: "anthropic"                # anthropic / openai / google
  model: "claude-sonnet-4-6"
  api_key: "sk-ant-..."                # Anthropic API Key
  max_tokens: 8192                     # claude-sonnet-4-6 최대 출력 토큰

schedule:
  day_of_week: "fri"                   # 매주 금요일
  hour: 20
  minute: 0
  timezone: "Asia/Seoul"

output:
  excel_dir: "./data"
  report_dir: "./data"
  chart_dir: "./data/charts"
  history_db: "./history.db"
  excel_prefix: "주가자금동향"          # 파일명: 주가자금동향_YYYYMMDD.xlsx
  report_prefix: "주가자금동향"
  max_weeks: 52                        # DB 최대 보관 주 수
```

### KIS API 발급

1. [한국투자증권 Developers](https://apiportal.koreainvestment.com) 가입
2. 앱 등록 → `app_key`, `app_secret` 발급
3. 모의투자 또는 실전 계좌번호 확인

---

## 4. 사용법

### 즉시 실행 (수동)

```bash
cd /path/to/WeeklyStocksTransaction

# 전체 파이프라인 즉시 실행 (약 5분)
.venv/bin/python main.py --run-now

# 보고서만 재생성 (기존 Excel + DB 활용, 약 5~8분)
.venv/bin/python regen_report.py

# 특정 날짜의 보고서 재생성
.venv/bin/python regen_report.py 20260228
```

### 스케줄러 모드 (레거시, 비권장)

```bash
# Python 프로세스를 계속 실행 상태로 유지해야 함
# Mac이 잠자기 상태일 경우 예약 실행이 누락될 수 있음
.venv/bin/python main.py
```

> **주의**: 스케줄러 모드는 Mac이 sleep 상태에 들어가면 예약 실행이 누락됩니다. 안정적인 자동 실행을 위해 아래의 **launchd 설정**을 사용하세요.

### 로그 확인

```bash
# 실시간 로그 모니터링 (launchd 실행 시)
tail -f /tmp/weeklystocks_pipeline.log

# 수동 실행 시
tail -f /path/to/WeeklyStocksTransaction/pipeline.log
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

## 5. launchd 설정 (Mac 자동 실행)

### 왜 launchd인가?

`python main.py` 스케줄러 모드는 Python 프로세스가 살아 있어야만 동작합니다. Mac이 잠자기(sleep) 상태로 전환되면 Python 프로세스도 일시정지되어, 예약 시각이 지나도 실행되지 않을 수 있습니다.

**macOS launchd**는 OS 레벨 작업 스케줄러로, Python 프로세스 없이도 예약 시각에 직접 파이프라인을 실행합니다. Mac이 잠자기 상태였더라도 예약 시각에 깨워서 실행합니다.

### APScheduler `misfire_grace_time` 동작

현재 코드(`scheduler.py`)에는 `misfire_grace_time=3600`이 설정되어 있어, Mac이 잠자기 후 **1시간 이내**에 깨어나면 즉시 실행됩니다. 하지만 1시간을 초과하면 실행이 완전히 누락됩니다.

| 시나리오 | APScheduler (스케줄러 모드) | launchd |
|---|---|---|
| 예약 시각에 Mac이 켜져 있음 | ✅ 실행 | ✅ 실행 |
| 예약 시각 ±1시간 내 잠자기 | ✅ 깨어나면 즉시 실행 | ✅ 실행 |
| 예약 시각 1시간 이후에 깨어남 | ❌ 누락 | ✅ 실행 |
| Mac 재부팅 후 자동 등록 | ❌ 수동 재시작 필요 | ✅ 자동 |

### 전제 조건: macOS 전체 디스크 접근 권한 (FDA)

프로젝트가 **외장 SSD**에 있는 경우, launchd 에이전트가 외장 볼륨에 접근하려면 아래 두 프로그램에 **전체 디스크 접근 권한**을 부여해야 합니다:

1. **시스템 설정** → **개인 정보 보호 및 보안** → **전체 디스크 접근 권한**
2. **+** 버튼 → `⌘+Shift+G` → 아래 경로 입력 후 각각 추가:
   - `/bin/bash` (launchd가 bash를 통해 명령 실행)
   - `/opt/homebrew/bin/python3.11` (Python이 외장 SSD의 패키지/스크립트 접근)
3. 두 항목 모두 토글 **켜기(ON)**

> **왜 venv Python이 아닌 homebrew Python인가?**
> `.venv/bin/python`은 심볼릭 링크로, Python 시작 시 `realpath()` 호출 과정에서 외장 SSD 접근 권한 문제가 발생합니다. homebrew의 실제 Python 바이너리(`/opt/homebrew/bin/python3.11`)를 직접 사용하고, `PYTHONPATH`로 venv의 site-packages를 지정하면 이 문제를 우회합니다.

### plist 파일 위치

```
~/Library/LaunchAgents/com.weeklystocks.pipeline.plist
```

### plist 내용

`/bin/bash -c`로 인라인 명령을 실행하며, `PYTHONPATH`로 venv 패키지 경로를 지정합니다. 경로를 실제 설치 경로에 맞게 수정하세요.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.weeklystocks.pipeline</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>-c</string>
        <string>export PYTHONPATH=/path/to/WeeklyStocksTransaction/.venv/lib/python3.11/site-packages; cd /path/to/WeeklyStocksTransaction &amp;&amp; /opt/homebrew/bin/python3.11 main.py --run-now</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Weekday</key>
        <integer>5</integer>   <!-- 5 = 금요일 (0=일, 1=월 ... 6=토) -->
        <key>Hour</key>
        <integer>20</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>/tmp/weeklystocks_pipeline.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/weeklystocks_pipeline_err.log</string>
    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
```

### 등록 및 관리

```bash
# 등록 (최초 1회)
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.weeklystocks.pipeline.plist

# 등록 확인 (PID 없고 종료 코드 0이면 정상 대기 중)
launchctl list | grep weeklystocks

# 즉시 수동 트리거 (테스트용)
launchctl kickstart -kp gui/$(id -u)/com.weeklystocks.pipeline

# plist 수정 후 재등록
launchctl bootout gui/$(id -u)/com.weeklystocks.pipeline
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.weeklystocks.pipeline.plist

# 레거시 명령어 (load/unload도 여전히 동작)
launchctl load ~/Library/LaunchAgents/com.weeklystocks.pipeline.plist
launchctl unload ~/Library/LaunchAgents/com.weeklystocks.pipeline.plist
```

### launchd 도입 후 달라지는 점

- `python main.py`(스케줄러 모드)를 상시 실행할 필요 없음 — 터미널을 닫아도 됨
- Mac 재부팅 후 자동 재등록 (`LaunchAgents` 폴더에 있으면 로그인 시 자동 로드)
- 수동 실행(`--run-now`)과 launchd 자동 실행은 완전히 독립적으로 동작
- 금요일 20:00 직전 수동 실행 시 두 프로세스가 겹칠 수 있으므로, 수동 실행은 20:05 이후 권장
- launchd 실행 시 로그는 `/tmp/weeklystocks_pipeline.log`에 기록됨

### 외장 SSD에서의 launchd 주의사항

macOS의 TCC(Transparency, Consent, and Control) 정책으로 인해 외장 볼륨 접근 시 다음 문제가 발생할 수 있습니다:

| 증상 | 원인 | 해결 |
|------|------|------|
| 종료코드 78, "service inactive" | launchd 도메인에서 서비스 비활성 | `launchctl enable gui/$(id -u)/com.weeklystocks.pipeline` |
| "Operation not permitted" | FDA 미부여 | `/bin/bash`, Python 모두 FDA 추가 |
| "realpath: Operation not permitted" | venv 심볼릭 링크 해석 실패 | homebrew Python 직접 사용 + PYTHONPATH |
| 로그 파일 미생성 | 외장 SSD 경로에 로그 기록 불가 | `/tmp/`에 로그 기록 |

---

## 6. 출력 결과물

```
data/
├── 주가자금동향_YYYYMMDD.xlsx       # Excel (5탭)
├── 주가자금동향_YYYYMMDD.md         # AI 보고서 (Markdown, ~27,000자)
└── charts/
    ├── cap_weight_kospi_YYYYMMDD.png    # 코스피 시총 비중 추이
    ├── cap_weight_kosdaq_YYYYMMDD.png   # 코스닥 시총 비중 추이
    ├── flow_kospi_YYYYMMDD.png          # 코스피 투자자 수급 추이
    ├── flow_kosdaq_YYYYMMDD.png         # 코스닥 투자자 수급 추이
    ├── sector_flow_kospi_YYYYMMDD.png   # 코스피 섹터별 수급
    └── sector_flow_kosdaq_YYYYMMDD.png  # 코스닥 섹터별 수급
```

### Excel 탭 구성

| 탭명 | 주요 컬럼 |
|------|-----------|
| 코스피 | 종목명, 시가총액, 현재가, PER, ROE, PBR, 배당, 기간별 등락률, 기관/외국인 수급, 다기간 누적 수급 |
| 코스닥 | 동일 |
| 증시정보 | 지수 현재가·등락, 주간 등락, 외국인·기관 순매수 합계 |
| 코스피 시총비중 | Top10/11~20/21~30/31~50/51~100/101~150/151~200 구간별 비중 추이 |
| 코스닥 시총비중 | 동일 (200위 이내 기준) |

### 다기간 수급 컬럼 자동 활성화

DB에 이력이 쌓이면 아래 컬럼이 자동으로 추가됩니다.

| 필요 이력 | 활성화 컬럼 |
|-----------|------------|
| 2주차부터 | 2주기관매매(억), 2주외국인매매(억) |
| 4주차부터 | 1개월기관매매(억), 1개월외국인매매(억) |
| 12주차부터 | 3개월기관매매(억), 3개월외국인매매(억) |

---

## 7. 라이선스

MIT License

Copyright (c) 2026

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
