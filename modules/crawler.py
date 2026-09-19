"""
crawler.py - 데이터 수집 모듈
■ 네이버 증권 JSON API(m.stock.naver.com): 전종목 시총·시세, 업종, 상위 종목 상세
■ KIS REST API(공식): 지수, 종목별 투자자 순매수(전종목·ETF), 휴장일
■ fchart: 기간별 등락률 (비동기)
※ KRX 스크래핑(pykrx)은 KRX 의 IP 제한(약관 위반 제재)으로 폐기했다 — SJAIINV-199
"""

import asyncio
import logging
import re
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import aiohttp
import pandas as pd
import requests
import yaml
from bs4 import BeautifulSoup


logger = logging.getLogger(__name__)

NAVER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9",
}

# KIS 토큰 파일 캐시 경로
_KIS_TOKEN_FILE = Path(".kis_token_cache.json")


# ─────────────────────────────────────────
# 날짜 유틸
# ─────────────────────────────────────────

def last_friday(base: Optional[date] = None) -> date:
    """기준일의 가장 최근 금요일 반환"""
    d = base or date.today()
    days_since_friday = (d.weekday() - 4) % 7
    return d - timedelta(days=days_since_friday)


def prev_bday(d: date, n: int) -> date:
    """d에서 n 영업일 이전 날짜"""
    cur = d
    count = 0
    while count < n:
        cur -= timedelta(days=1)
        if cur.weekday() < 5:
            count += 1
    return cur


# ─────────────────────────────────────────
# KIS API 클라이언트
# ─────────────────────────────────────────

class KISClient:
    BASE = "https://openapi.koreainvestment.com:9443"

    def __init__(self, app_key: str, app_secret: str):
        self.app_key = app_key
        self.app_secret = app_secret

    def _get_token(self) -> str:
        """파일 기반 토큰 캐시 (24시간 유효, 1분당 1회 발급 제한 우회)"""
        import json as _json

        now = time.time()
        # 파일 캐시 읽기
        if _KIS_TOKEN_FILE.exists():
            try:
                cache = _json.loads(_KIS_TOKEN_FILE.read_text())
                if cache.get("app_key") == self.app_key and now < cache.get("expires", 0) - 120:
                    return cache["token"]
            except Exception:
                pass

        # 새 토큰 발급
        resp = requests.post(
            f"{self.BASE}/oauth2/tokenP",
            json={"grant_type": "client_credentials",
                  "appkey": self.app_key, "appsecret": self.app_secret},
            timeout=10,
        )
        data = resp.json()
        if "access_token" not in data:
            raise RuntimeError(f"KIS 토큰 발급 실패: {data}")

        cache = {"token": data["access_token"],
                 "expires": now + 86000,  # ~24시간
                 "app_key": self.app_key}
        _KIS_TOKEN_FILE.write_text(_json.dumps(cache))
        logger.info("KIS 토큰 발급 및 캐시 저장 성공")
        return data["access_token"]

    def _headers(self, tr_id: str) -> dict:
        return {
            "authorization": f"Bearer {self._get_token()}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
        }

    def get(self, tr_id: str, path: str, params: dict) -> dict:
        r = requests.get(f"{self.BASE}{path}", headers=self._headers(tr_id),
                         params=params, timeout=15)
        return r.json()

    # ── 개별 종목 현재가/펀더멘털 ──
    def get_stock_price(self, ticker: str, market: str = "J") -> dict:
        """개별 종목 현재가 + PBR + 배당수익률"""
        d = self.get(
            "FHKST01010100",
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            {"FID_COND_MRKT_DIV_CODE": market, "FID_INPUT_ISCD": ticker},
        )
        if d.get("rt_cd") != "0":
            return {}
        return d.get("output", {})

    # ── KOSPI/KOSDAQ 지수 ──
    def get_index_daily(self, iscd: str) -> dict:
        """
        지수 현재가
        iscd: "0001"=KOSPI, "1001"=KOSDAQ
        """
        d = self.get(
            "FHPUP02100000",
            "/uapi/domestic-stock/v1/quotations/inquire-index-price",
            {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": iscd},
        )
        if d.get("rt_cd") != "0":
            return {}
        return d.get("output", {})

    # ── 지수 기간별 시세 ──
    def get_index_period(self, iscd: str, start: str, end: str) -> list[dict]:
        """
        지수 일별 시세
        iscd: "0001"=KOSPI, "1001"=KOSDAQ
        """
        d = self.get(
            "FHKUP03500100",  # FHKUP03500200 은 다른 TR — rt_cd=2 로 항상 빈 목록이었다 (2026-09-19 실측)
            "/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice",
            {
                "FID_COND_MRKT_DIV_CODE": "U",
                "FID_INPUT_ISCD": iscd,
                "FID_INPUT_DATE_1": start,
                "FID_INPUT_DATE_2": end,
                "FID_PERIOD_DIV_CODE": "D",
            },
        )
        if d.get("rt_cd") != "0":
            return []
        return d.get("output2", [])

    # ── 투자자별 매매동향 (지수) ──
    def get_investor_flow_market(self, market: str = "J") -> dict:
        """시장 전체 투자자별 당일 매매동향"""
        d = self.get(
            "FHPST01710000",
            "/uapi/domestic-stock/v1/quotations/inquire-investor",
            {"FID_COND_MRKT_DIV_CODE": market, "FID_INPUT_ISCD": "0001"},
        )
        if d.get("rt_cd") != "0":
            return {}
        return d.get("output", {})


# ─────────────────────────────────────────
# 네이버금융 크롤링 (전종목)
# ─────────────────────────────────────────

NAVER_API = "https://m.stock.naver.com/api"
NAVER_PAGE_SIZE = 100


def _naver_json(path: str, params: Optional[dict] = None) -> dict:
    """네이버 증권 JSON API 호출.

    finance.naver.com 웹페이지는 2026-09 초에 Npay 증권(JavaScript 렌더링)으로
    이전돼 HTML 파싱이 0건이 됐다 (SJAIINV-197). 신규 서비스가 내부적으로 쓰는
    이 JSON API 가 대체 경로다. 비공식이므로 응답 구조가 바뀔 수 있다 —
    호출자는 빈 결과를 명시적 오류로 취급할 것.
    """
    r = requests.get(f"{NAVER_API}{path}", params=params, headers=NAVER_HEADERS, timeout=10)
    r.raise_for_status()
    return r.json()


def _to_float(v) -> Optional[float]:
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def crawl_naver_market(market_code: int, max_pages: Optional[int] = None) -> pd.DataFrame:
    """
    네이버 증권 시장 전종목 시총 순위 수집 (JSON API)
    market_code: 0=KOSPI, 1=KOSDAQ
    """
    market = "KOSPI" if market_code == 0 else "KOSDAQ"
    path = f"/stocks/marketValue/{market}"

    first = _naver_json(path, {"page": 1, "pageSize": NAVER_PAGE_SIZE})
    total = int(first.get("totalCount") or 0)
    last_page = -(-total // NAVER_PAGE_SIZE) if total else 1
    if max_pages:
        last_page = min(last_page, max_pages)
    logger.info(f"네이버 증권 {market} 수집: {total}종목 / {last_page}페이지")

    all_records = []
    for page in range(1, last_page + 1):
        try:
            data = first if page == 1 else _naver_json(
                path, {"page": page, "pageSize": NAVER_PAGE_SIZE})
            for s in data.get("stocks", []):
                value_won = _to_float(s.get("accumulatedTradingValueRaw"))
                cap_won = _to_float(s.get("marketValueRaw"))
                all_records.append({
                    "순위": len(all_records) + 1,
                    "티커": str(s.get("itemCode", "")),
                    "종목명": s.get("stockName", ""),
                    "종목유형": s.get("stockEndType", ""),  # stock · etf · etn … (수급 수집 대상 구분용)
                    "현재가": _to_float(s.get("closePriceRaw")),
                    "전일대비": s.get("compareToPreviousClosePrice", ""),
                    "등락률_당일": _to_float(s.get("fluctuationsRatio")),
                    "시가총액(억)": round(cap_won / 1e8, 2) if cap_won is not None else None,
                    "거래량": _to_float(s.get("accumulatedTradingVolumeRaw")),
                    # Raw 는 원 단위 실제 거래대금 — 기존의 거래량×현재가 근사보다 정확하다
                    "거래대금(억)": round(value_won / 1e8, 2) if value_won is not None else None,
                })
            if page % 10 == 0:
                logger.info(f"  페이지 {page}/{last_page} 완료 ({len(all_records)}개)")
            if page < last_page:
                time.sleep(0.3)
        except Exception as e:
            logger.warning(f"  페이지 {page} 실패: {e}")

    if not all_records:
        # 0건을 그대로 넘기면 하류에서 KeyError('티커') 로 죽어 원인 파악이 늦어진다
        raise RuntimeError(
            f"네이버 증권 {market} 전종목 수집 0건 — API 응답 구조 변경 가능 (SJAIINV-197)")

    df = pd.DataFrame(all_records)
    df = df[df["티커"].str.len() == 6].reset_index(drop=True)
    logger.info(f"네이버 증권 전종목 완료: {len(df)}개")
    return df


# ─────────────────────────────────────────
# 네이버금융 업종 그룹 → 섹터 매핑
# ─────────────────────────────────────────

def build_sector_map() -> dict:
    """네이버 증권 업종 API 로 {ticker: sector_name} 매핑 빌드.

    업종 79개의 이름·번호 체계는 폐지된 sise_group 페이지와 동일하다
    (2026-09-19 weekly_sector 이력과 79/79 일치 확인) — 섹터 이력이 끊기지 않는다.
    """
    ticker_sector: dict = {}

    # 1. 업종 목록
    try:
        groups = _naver_json("/stocks/industry",
                             {"page": 1, "pageSize": NAVER_PAGE_SIZE}).get("groups", [])
        sectors = [(g["no"], g["name"]) for g in groups if g.get("no") and g.get("name")]
    except Exception as e:
        logger.error(f"업종 목록 수집 실패: {e}")
        return {}

    logger.info(f"업종 수: {len(sectors)}개, 업종별 종목 매핑 빌드 중...")

    # 2. 업종별 구성 종목 (100개 초과 업종은 페이지를 넘긴다)
    for no, sector_name in sectors:
        try:
            page = 1
            while True:
                data = _naver_json(f"/stocks/industry/{no}",
                                   {"page": page, "pageSize": NAVER_PAGE_SIZE})
                stocks = data.get("stocks", [])
                for st in stocks:
                    ticker = str(st.get("itemCode", ""))
                    if len(ticker) == 6:
                        ticker_sector[ticker] = sector_name
                time.sleep(0.2)
                total = int(data.get("totalCount") or 0)
                if not stocks or page * NAVER_PAGE_SIZE >= total:
                    break
                page += 1
        except Exception as e:
            logger.warning(f"업종 {sector_name}({no}) 수집 실패: {e}")

    logger.info(f"섹터 매핑 완료: {len(ticker_sector)}개 종목")
    return ticker_sector


# ─────────────────────────────────────────
# 네이버금융 개별 종목 (섹터 + 재무) - 미사용 (섹터는 build_sector_map으로 대체)
# ─────────────────────────────────────────

async def _fetch_naver_stock(session: aiohttp.ClientSession, ticker: str) -> dict:
    """개별 종목 섹터 + 재무 정보"""
    result = {"티커": ticker, "섹터": None,
              "PBR": None, "배당수익률": None,
              "연간_매출액": None, "연간_영업이익": None,
              "영업이익률": None, "연간_ROE": None,
              "부채비율": None, "유동비율": None,
              "매출액_증가율": None, "영업이익_증가율": None}
    try:
        url = f"https://finance.naver.com/item/main.naver?code={ticker}"
        async with session.get(url, headers=NAVER_HEADERS,
                               timeout=aiohttp.ClientTimeout(total=10)) as resp:
            html = await resp.text(encoding="euc-kr", errors="replace")

        soup = BeautifulSoup(html, "lxml")

        # 섹터(업종)
        em = soup.select_one("em.industry_type") or soup.find("em", class_="industry_type")
        if not em:
            # 대안: 업종명 찾기
            for td in soup.find_all("td"):
                if "업종" in td.get_text():
                    next_td = td.find_next_sibling("td")
                    if next_td:
                        result["섹터"] = next_td.get_text(strip=True)
                        break
        else:
            result["섹터"] = em.get_text(strip=True)

        # PBR, 배당수익률 from summary
        for tr in soup.select("table.per_table tr"):
            tds = tr.find_all("td")
            if len(tds) >= 2:
                label = tds[0].get_text(strip=True)
                val_text = tds[1].get_text(strip=True).replace(",", "").replace("%", "")
                try:
                    val = float(val_text)
                except ValueError:
                    continue
                if "PBR" in label:
                    result["PBR"] = val
                elif "배당수익률" in label or "배당" in label:
                    result["배당수익률"] = val

    except Exception as e:
        logger.debug(f"네이버금융 개별 크롤링 실패 ({ticker}): {e}")

    return result


async def crawl_naver_individual(tickers: list[str], delay: float = 0.35) -> pd.DataFrame:
    """비동기 네이버금융 개별 종목 크롤링"""
    results = []
    connector = aiohttp.TCPConnector(limit=8, limit_per_host=3)
    async with aiohttp.ClientSession(connector=connector) as session:
        for i, ticker in enumerate(tickers):
            row = await _fetch_naver_stock(session, ticker)
            results.append(row)
            if (i + 1) % 100 == 0:
                logger.info(f"개별 크롤링 진행: {i+1}/{len(tickers)}")
            await asyncio.sleep(delay)
    return pd.DataFrame(results)


# ─────────────────────────────────────────
# KIS API 데이터 수집
# ─────────────────────────────────────────

def collect_kis_market_info(kis: KISClient) -> dict:
    """KIS API로 KOSPI/KOSDAQ 지수 + 투자자별 매매 수집"""
    info = {}

    index_map = {
        "KOSPI": ("0001", "J"),
        "KOSDAQ": ("1001", "Q"),
    }

    for market_name, (iscd, mkt_code) in index_map.items():
        try:
            idx = kis.get_index_daily(iscd)
            if idx:
                close = _safe_float(idx.get("bstp_nmix_prpr"))
                # KOSPI: bstp_nmix_prdy_vrss 없을 수 있음 → None 처리
                daily_pt = _safe_float(idx.get("bstp_nmix_prdy_vrss"))
                daily_pct = _safe_float(idx.get("bstp_nmix_prdy_ctrt")) or \
                            _safe_float(idx.get("prdy_ctrt"))
                info[f"{market_name}_index"] = {
                    "종가": close,
                    "전일대비": daily_pt,
                    "등락률": daily_pct,
                    "거래대금": _safe_float(idx.get("acml_tr_pbmn")),
                }
            time.sleep(0.3)
        except Exception as e:
            logger.error(f"KIS 지수 조회 실패 ({market_name}): {e}")

    return info


def collect_kis_pbr_for_top(kis: KISClient, tickers: list[str],
                             market_code: str = "J", limit: int = 200) -> pd.DataFrame:
    """상위 N개 종목의 PBR, 배당수익률 KIS API로 조회"""
    results = []
    tickers = tickers[:limit]
    for i, ticker in enumerate(tickers):
        try:
            d = kis.get_stock_price(ticker, market_code)
            if d:
                results.append({
                    "티커": ticker,
                    "PBR_KIS": _safe_float(d.get("pbr")),
                    "배당수익률_KIS": _safe_float(d.get("dvdt_rate12")),
                })
            if (i + 1) % 20 == 0:
                logger.info(f"KIS PBR 조회 진행: {i+1}/{len(tickers)}")
            time.sleep(0.1)
        except Exception as e:
            logger.debug(f"KIS PBR 조회 실패 ({ticker}): {e}")
    return pd.DataFrame(results)


def _safe_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(str(v).replace(",", ""))
    except (ValueError, TypeError):
        return None


# ─────────────────────────────────────────
# 주간 등락률 계산 (네이버금융 주간차트 이용)
# ─────────────────────────────────────────

def get_weekly_return_from_naver(ticker: str, base_date: date, prev_date: date) -> Optional[float]:
    """네이버금융 일별 차트에서 주간 등락률 계산"""
    try:
        # 네이버금융 일별 시세
        url = f"https://finance.naver.com/item/sise_day.naver?code={ticker}"
        r = requests.get(url, headers=NAVER_HEADERS, timeout=8)
        soup = BeautifulSoup(r.text, "lxml")
        rows = soup.select("table.type2 tr")
        date_price = {}
        for row in rows:
            tds = row.find_all("td")
            if len(tds) < 2:
                continue
            date_str = tds[0].get_text(strip=True)
            price_str = tds[1].get_text(strip=True).replace(",", "")
            try:
                d = date.fromisoformat(date_str) if "-" in date_str else None
                if d:
                    date_price[d] = float(price_str)
            except (ValueError, TypeError):
                pass

        # 기준일과 1주 전 가격
        base_price = date_price.get(base_date)
        prev_price = date_price.get(prev_date)
        if base_price and prev_price and prev_price > 0:
            return (base_price - prev_price) / prev_price * 100
    except Exception:
        pass
    return None


# ─────────────────────────────────────────
# fchart API 기간별 등락률 (비동기)
# ─────────────────────────────────────────

def _parse_fchart_closes(content: bytes) -> list:
    """네이버 fchart XML에서 종가 리스트 추출 (시간순 오름차순)"""
    closes = []
    try:
        text = content.decode("euc-kr", errors="replace")
        for m in re.finditer(r'data="(\d{8})\|[^|]+\|[^|]+\|[^|]+\|([^|]+)\|', text):
            try:
                closes.append(float(m.group(2)))
            except ValueError:
                closes.append(None)
    except Exception:
        pass
    return closes


def _calc_period_return(closes: list, n_days: int) -> Optional[float]:
    """n_days 거래일 이전 대비 수익률(%) 계산"""
    if len(closes) < n_days + 1:
        return None
    cur = closes[-1]
    prev = closes[-(n_days + 1)]
    if cur is not None and prev and prev > 0:
        return round((cur - prev) / prev * 100, 2)
    return None


async def _fetch_fchart_one(session: aiohttp.ClientSession, ticker: str, week_days: int = 5) -> dict:
    """fchart API 단일 종목 기간별 등락률 조회
    week_days: 이번 주 거래일 수 (월=1, 화=2, ..., 금=5)
    """
    result = {
        "티커": ticker,
        "1주등락률_fchart": None,
        "1개월등락률": None,
        "3개월등락률": None,
        "6개월등락률": None,
    }
    try:
        # count=135 → 5일(1주), 22일(1개월), 65일(3개월), 130일(6개월) 계산 가능
        url = (
            "https://fchart.stock.naver.com/sise.nhn"
            f"?symbol={ticker}&timeframe=day&count=135&requestType=0"
        )
        async with session.get(
            url, headers=NAVER_HEADERS, timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            content = await resp.read()
        closes = _parse_fchart_closes(content)
        if closes:
            result["1주등락률_fchart"]  = _calc_period_return(closes, week_days)
            result["1개월등락률"]       = _calc_period_return(closes, 22)
            result["3개월등락률"]       = _calc_period_return(closes, 65)
            result["6개월등락률"]       = _calc_period_return(closes, 130)
    except Exception as e:
        logger.debug(f"fchart 실패 ({ticker}): {e}")
    return result


async def _crawl_period_returns_async(tickers: list, week_days: int = 5) -> pd.DataFrame:
    """전종목 기간별 등락률 비동기 수집"""
    sem = asyncio.Semaphore(20)

    async def fetch_with_sem(session: aiohttp.ClientSession, ticker: str) -> dict:
        async with sem:
            r = await _fetch_fchart_one(session, ticker, week_days=week_days)
            await asyncio.sleep(0.03)
            return r

    connector = aiohttp.TCPConnector(limit=20, limit_per_host=10)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [fetch_with_sem(session, t) for t in tickers]
        raw = await asyncio.gather(*tasks, return_exceptions=True)

    valid = []
    for i, r in enumerate(raw):
        if isinstance(r, dict):
            valid.append(r)
        else:
            valid.append({"티커": tickers[i]})

    logger.info(f"fchart 기간별 등락률 수집 완료: {len(valid)}개")
    return pd.DataFrame(valid) if valid else pd.DataFrame()


def crawl_period_returns_all(tickers: list, week_days: int = 5) -> pd.DataFrame:
    """전종목 기간별 등락률 수집 (동기 래퍼)"""
    logger.info(f"fchart API 기간별 등락률 수집: {len(tickers)}개 종목 (주간등락: {week_days}거래일)")
    try:
        return asyncio.run(_crawl_period_returns_async(tickers, week_days=week_days))
    except RuntimeError:
        # 이미 실행 중인 이벤트 루프가 있는 경우
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_crawl_period_returns_async(tickers, week_days=week_days))
        finally:
            loop.close()


# ─────────────────────────────────────────
# 네이버금융 개별 종목 상세 (재무 + 투자자)
# ─────────────────────────────────────────

def _num(text) -> Optional[float]:
    """'11.66배' · '0.64%' · '+2,746,972' · '-' → float 또는 None."""
    if text is None:
        return None
    m = re.search(r"[-+]?\d[\d,]*\.?\d*", str(text))
    return _to_float(m.group(0).replace("+", "")) if m else None


def _parse_naver_integration(d: dict, cur_price: Optional[float] = None,
                             investor_days: int = 5) -> dict:
    """integration 응답 → PER·PBR·배당수익률 + 최근 investor_days일 외국인/기관 매매(억)."""
    result: dict = {}
    info = {t.get("code"): t.get("value") for t in d.get("totalInfos", [])}
    for code, col in (("per", "PER_NAVER"), ("pbr", "PBR_NAVER"),
                      ("dividendYieldRatio", "배당수익률_NAVER")):
        v = _num(info.get(code))
        if v is not None:
            result[col] = v

    # dealTrendInfos 는 최신 거래일이 먼저 온다 — 앞에서부터 investor_days 일만 합산
    fore_sum = inst_sum = 0.0
    count = 0
    for row in d.get("dealTrendInfos", [])[:investor_days]:
        fore_v = _num(row.get("foreignerPureBuyQuant"))
        inst_v = _num(row.get("organPureBuyQuant"))
        if fore_v is None or inst_v is None:
            continue
        fore_sum += fore_v
        inst_sum += inst_v
        count += 1
    if count > 0 and cur_price and cur_price > 0:
        # 주(株) × 현재가 / 1억 = 억원
        result["1주외국인매매"] = round(fore_sum * cur_price / 100_000_000, 2)
        result["1주기관매매"] = round(inst_sum * cur_price / 100_000_000, 2)
    return result


def _parse_naver_finance(d: dict) -> dict:
    """finance/annual 응답 → 최근 확정 연도 실적과 전년 대비 증가율.

    추정치 열(isConsensus=Y)은 제외한다 — 최근 실적으로 쓰면 컨센서스가 섞인다.
    """
    fi = d.get("financeInfo") or {}
    confirmed = sorted(t["key"] for t in fi.get("trTitleList", [])
                       if t.get("isConsensus") == "N" and t.get("key"))
    if not confirmed:
        return {}
    curr_key = confirmed[-1]
    prev_key = confirmed[-2] if len(confirmed) > 1 else None
    rows = {r.get("title"): r.get("columns", {}) for r in fi.get("rowList", [])}

    def val(title: str, key: Optional[str]) -> Optional[float]:
        if key is None:
            return None
        return _num((rows.get(title, {}).get(key) or {}).get("value"))

    result: dict = {}
    for title, col in (("매출액", "연간_매출액"), ("영업이익", "연간_영업이익"),
                       ("영업이익률", "영업이익률"), ("ROE", "연간_ROE"), ("부채비율", "부채비율")):
        v = val(title, curr_key)
        if v is not None:
            result[col] = v

    for title, col in (("매출액", "매출액_증가율"), ("영업이익", "영업이익_증가율")):
        curr, prev = val(title, curr_key), val(title, prev_key)
        if curr and prev and prev != 0:
            result[col] = round((curr - prev) / abs(prev) * 100, 2)
    return result


def crawl_naver_stock_details(
    tickers: list,
    prices: dict,
    n: int = 200,
    delay: float = 0.35,
    investor_days: int = 5,
) -> pd.DataFrame:
    """
    상위 N개 종목의 네이버 증권 상세 데이터 수집 (JSON API, 종목당 2콜)
    prices: {ticker: current_price} - 투자자 주수 → 억원 변환용
    investor_days: 투자자 매매 합산 거래일 수 (중간분석 시 이번 주 거래일 수)
    수집 항목: PER, PBR, 배당수익률, 연간 재무실적, 1주 기관/외국인 매매
    """
    results = []
    tickers = tickers[:n]
    for i, ticker in enumerate(tickers):
        row: dict = {"티커": ticker}
        try:
            row.update(_parse_naver_integration(
                _naver_json(f"/stock/{ticker}/integration"),
                prices.get(ticker), investor_days=investor_days))
            row.update(_parse_naver_finance(_naver_json(f"/stock/{ticker}/finance/annual")))
        except Exception as e:
            logger.debug(f"네이버 상세 수집 실패 ({ticker}): {e}")
        results.append(row)
        if (i + 1) % 50 == 0:
            logger.info(f"네이버 상세 수집: {i+1}/{len(tickers)}")
        time.sleep(delay)
    logger.info(f"네이버 상세 수집 완료: {len(results)}개")
    return pd.DataFrame(results) if results else pd.DataFrame()


# ─────────────────────────────────────────
# KIS 종목별 투자자 순매수 (공식 API, 종목당 1콜)
# ─────────────────────────────────────────
#
# KRX(pykrx 스크래핑)는 2026-09-19 에 이 머신의 IP 를 "자동화 수단을 통한 비정상 대량
# 조회"로 1일간 제한했다 — 이용약관 제10조 제2호가 자동화 수집을 금지한다 (SJAIINV-199).
# KIS 는 사용자 본인 계정의 공식 API 다. 09-04 주 대조: 기관은 KRX 기반 DB 값과
# 소수점까지 일치, 외국인은 0.4~2% 차이(KIS 값에 기타외국인이 포함된 것으로 보인다).

KIS_INVESTOR_TR = "FHKST01010900"
KIS_INVESTOR_PATH = "/uapi/domestic-stock/v1/quotations/inquire-investor"
KIS_RATE_LIMIT_CODE = "EGW00201"  # 초당 거래건수 초과


def fetch_kis_investor_daily(kis: KISClient, ticker: str) -> list[dict]:
    """종목의 최근 30거래일 투자자별 순매수(일별). KOSPI·KOSDAQ·ETF 모두 시장코드 J."""
    params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker}
    d = kis.get(KIS_INVESTOR_TR, KIS_INVESTOR_PATH, params)
    if d.get("msg_cd") == KIS_RATE_LIMIT_CODE:
        time.sleep(1.0)
        d = kis.get(KIS_INVESTOR_TR, KIS_INVESTOR_PATH, params)
    if d.get("rt_cd") != "0":
        raise RuntimeError(f"KIS 투자자 조회 실패({ticker}): {str(d.get('msg1', '')).strip()}")
    return d.get("output") or []


def _sum_kis_flows(rows: list[dict], fromdate: str, todate: str,
                   with_individual: bool = False) -> Optional[dict]:
    """[fromdate, todate] 구간의 기관·외국인 순매수 거래대금 합(억원). 해당 일자가 없으면 None."""
    sel = [r for r in rows if fromdate <= r.get("stck_bsop_date", "") <= todate]
    if not sel:
        return None

    def total(key: str) -> float:
        # tr_pbmn 단위는 백만원 → /100 = 억원
        return round(sum(_to_float(r.get(key)) or 0.0 for r in sel) / 100, 2)

    out = {"1주기관매매": total("orgn_ntby_tr_pbmn"), "1주외국인매매": total("frgn_ntby_tr_pbmn")}
    if with_individual:
        out["1주개인매매"] = total("prsn_ntby_tr_pbmn")
    return out


def crawl_kis_investor_flows(kis: KISClient, tickers_by_market: dict, fromdate: str,
                             todate: str, delay: float = 0.12) -> pd.DataFrame:
    """전종목 기관·외국인 주간 순매수(거래대금, 억원) — 종목당 1콜.

    Returns: DataFrame [티커, 시장, 1주기관매매, 1주외국인매매]
    Raises: RuntimeError (빈 결과) — 호출자가 폴백을 결정한다.
    개별 종목 실패는 건너뛴다. delay 0.12초 ≈ 초당 8콜 (KIS 실전 한도는 초당 20콜).
    """
    records = []
    failed = 0
    for market, tickers in tickers_by_market.items():
        for i, ticker in enumerate(tickers):
            try:
                flows = _sum_kis_flows(fetch_kis_investor_daily(kis, ticker), fromdate, todate)
                if flows is not None:
                    records.append({"티커": ticker, "시장": market, **flows})
            except Exception as e:
                failed += 1
                logger.debug(f"KIS 수급 조회 실패 ({ticker}): {e}")
            if (i + 1) % 500 == 0:
                logger.info(f"KIS 수급 수집 ({market}): {i + 1}/{len(tickers)}")
            time.sleep(delay)

    if not records:
        raise RuntimeError(f"KIS 전종목 수급 빈 응답 ({fromdate}~{todate}, 실패 {failed}건)")
    result = pd.DataFrame(records, columns=["티커", "시장", "1주기관매매", "1주외국인매매"])
    logger.info(f"KIS 전종목 수급 수집 완료: {len(result)}개 종목 (실패 {failed}건)")
    return result


def crawl_kis_etf_flows(kis: KISClient, etf_universe: pd.DataFrame, fromdate: str, todate: str,
                        top_n: int = 120, delay: float = 0.12):
    """ETF 기관·외국인 주간 순매수 — 거래대금 상위 top_n ETF 를 KIS 로 조회.

    etf_universe: 네이버 증권 전종목 표에서 ETF 만 추린 것 [티커, 종목명, 거래대금(억)].
    Returns: (etf_flows[티커,종목명,1주기관매매,1주외국인매매], agg{범위,외국인,기관,개인})
    agg 는 **수집한 상위 N 의 합계**다 — KRX 가 주던 ETF 시장 전체 집계가 아니므로
    그렇게 라벨링한다.
    Raises: RuntimeError (유니버스·결과가 비었을 때) — 호출자가 ETF 섹션을 생략한다.
    """
    if etf_universe is None or etf_universe.empty:
        raise RuntimeError("ETF 유니버스가 빈 표 — 네이버 전종목 표에 ETF 가 없다")
    top = etf_universe.sort_values("거래대금(억)", ascending=False).head(top_n)

    rows = []
    agg = {"외국인": 0.0, "기관": 0.0, "개인": 0.0}
    for ticker, name in zip(top["티커"], top["종목명"]):
        try:
            flows = _sum_kis_flows(fetch_kis_investor_daily(kis, ticker), fromdate, todate,
                                   with_individual=True)
            if flows is not None:
                rows.append({"티커": ticker, "종목명": name,
                             "1주기관매매": flows["1주기관매매"],
                             "1주외국인매매": flows["1주외국인매매"]})
                agg["기관"] += flows["1주기관매매"]
                agg["외국인"] += flows["1주외국인매매"]
                agg["개인"] += flows["1주개인매매"]
        except Exception as e:
            logger.debug(f"KIS ETF 수급 조회 실패 ({ticker}): {e}")
        time.sleep(delay)

    if not rows:
        raise RuntimeError(f"KIS ETF 수급 빈 응답 ({fromdate}~{todate})")
    etf_flows = pd.DataFrame(rows, columns=["티커", "종목명", "1주기관매매", "1주외국인매매"])
    agg = {"범위": f"거래대금 상위 {len(rows)} ETF 합계",
           **{k: round(v, 2) for k, v in agg.items()}}
    logger.info(f"KIS ETF 수급 수집 완료: {len(etf_flows)}개 ETF")
    return etf_flows, agg


# ─────────────────────────────────────────
# 메인 수집 함수
# ─────────────────────────────────────────

DAY_NAMES_KR = ["월", "화", "수", "목", "금", "토", "일"]


def collect_all(config: dict, midweek: bool = False) -> dict:
    """
    전체 데이터 수집 파이프라인
    midweek=True 이면 이번 주 월~오늘까지의 거래일만 분석 (--run-now 용)
    Returns:
        {
            'kospi': DataFrame,
            'kosdaq': DataFrame,
            'market_info': dict,
            'base_date': str,      # 표시/파일명용 (midweek: 오늘, 정규: 금요일)
            'week_date': str,      # DB 저장용 (항상 이번 주 금요일)
            'week_start': str,
            'biz_days': int,       # 이번 주 거래일 수
            'is_midweek': bool,
            'period_label': str,   # "3/2(월)~3/4(수)" 형태
        }
    """
    kis_cfg = config.get("kis", {})
    kis = KISClient(kis_cfg.get("app_key", ""), kis_cfg.get("app_secret", ""))

    today = date.today()
    weekday = today.weekday()  # Mon=0 ... Fri=4, Sat=5, Sun=6

    if midweek and weekday < 5:
        # 주중 실행: 이번 주 월~오늘까지만 분석
        biz_days = weekday + 1           # 월=1, 화=2, 수=3, 목=4, 금=5
        base = today                      # 표시용 기준일 = 오늘
        week_friday = today + timedelta(days=(4 - weekday))  # 이번 주 금요일 (DB key)
        week_start_dt = today - timedelta(days=weekday)      # 이번 주 월요일
        is_midweek = biz_days < 5
        period_label = (
            f"{week_start_dt.month}/{week_start_dt.day}({DAY_NAMES_KR[week_start_dt.weekday()]})"
            f"~{base.month}/{base.day}({DAY_NAMES_KR[base.weekday()]})"
        )
    else:
        # 금요일 또는 주말 실행: 기존 로직
        biz_days = 5
        base = last_friday(today)
        week_friday = base
        week_start_dt = base - timedelta(days=4)  # 해당 주 월요일
        is_midweek = False
        period_label = ""

    week_start = prev_bday(base, biz_days)  # biz_days 영업일 전

    base_str = base.strftime("%Y%m%d")
    week_start_str = week_start.strftime("%Y%m%d")
    week_date_str = week_friday.strftime("%Y%m%d")

    logger.info(
        f"기준일: {base_str}, 주간 시작: {week_start_str}, "
        f"거래일수: {biz_days}, 중간분석: {is_midweek}"
    )

    result = {}

    # ── 1. 네이버금융 전종목 수집 ──
    for market_code, market_name in [(0, "kospi"), (1, "kosdaq")]:
        logger.info(f"[{market_name.upper()}] 네이버금융 전종목 수집...")
        df = crawl_naver_market(market_code)
        df["시장"] = market_name.upper()
        result[market_name] = df

    # ── 2. 업종 그룹 페이지 → 섹터 매핑 (~80 페이지, 약 30초) ──
    logger.info("네이버금융 업종 그룹에서 섹터 매핑 빌드...")
    sector_map = build_sector_map()
    for market_name in ["kospi", "kosdaq"]:
        df = result.get(market_name, pd.DataFrame())
        if not df.empty and sector_map:
            df = df.copy()
            df["섹터"] = df["티커"].map(sector_map)
            result[market_name] = df

    # ── 4. KIS API: 지수 정보 ──
    logger.info("KIS API 지수 수집...")
    try:
        market_info = collect_kis_market_info(kis)
        result["market_info"] = market_info
    except Exception as e:
        logger.error(f"KIS 지수 수집 실패: {e}")
        result["market_info"] = {}

    # ── 5. 전종목 기간별 등락률 (fchart API, 비동기) ──
    all_tickers = []
    for market_name in ["kospi", "kosdaq"]:
        df = result.get(market_name, pd.DataFrame())
        if not df.empty and "티커" in df.columns:
            all_tickers.extend(df["티커"].tolist())

    if all_tickers:
        try:
            logger.info(f"fchart 기간별 등락률 수집: {len(all_tickers)}개 종목...")
            period_df = crawl_period_returns_all(all_tickers, week_days=biz_days)

            if not period_df.empty:
                for market_name in ["kospi", "kosdaq"]:
                    df = result.get(market_name, pd.DataFrame())
                    if df.empty:
                        continue
                    cols = ["티커"] + [c for c in ["1주등락률_fchart", "1개월등락률", "3개월등락률", "6개월등락률"] if c in period_df.columns]
                    df = df.merge(period_df[cols], on="티커", how="left")
                    # fchart 1주 등락률 → 기존 1주등락률 대체 (당일 기준보다 정확)
                    if "1주등락률_fchart" in df.columns:
                        df["1주등락률"] = df["1주등락률_fchart"]
                        df.drop(columns=["1주등락률_fchart"], inplace=True, errors="ignore")
                    result[market_name] = df
        except Exception as e:
            logger.error(f"fchart 기간별 등락률 수집 실패: {e}")

    # ── 5.5 KIS 전종목 투자자 순매수 (기관·외국인, 종목당 1콜) ──
    # KRX 스크래핑(pykrx)은 KRX 가 약관 위반으로 IP 를 제한해 폐기했다 (SJAIINV-199).
    monday_str = week_start_dt.strftime("%Y%m%d")

    def _of_type(market_name: str, end_type: str) -> pd.DataFrame:
        df = result.get(market_name, pd.DataFrame())
        if df.empty or "종목유형" not in df.columns:
            return pd.DataFrame()
        return df[df["종목유형"] == end_type]

    try:
        tickers_by_market = {
            m.upper(): list(_of_type(m, "stock")["티커"]) for m in ["kospi", "kosdaq"]
        }
        result["investor_flows"] = crawl_kis_investor_flows(kis, tickers_by_market,
                                                            monday_str, base_str)
        result["flow_source"] = "kis"
    except Exception as e:
        logger.warning(f"KIS 전종목 수급 실패({e}) → Naver 상위200 폴백")
        result["investor_flows"] = pd.DataFrame()
        result["flow_source"] = "naver"

    # ── 5.6 KIS ETF 수급 (거래대금 상위 ETF, best-effort) ──
    try:
        etf_universe = pd.concat([_of_type(m, "etf") for m in ["kospi", "kosdaq"]],
                                 ignore_index=True)
        etf_flows, etf_agg = crawl_kis_etf_flows(kis, etf_universe, monday_str, base_str)
        result["etf_flows"] = etf_flows
        result["etf_market_agg"] = etf_agg
    except Exception as e:
        logger.warning(f"ETF 수급 수집 실패({e}) → ETF 섹션 생략")
        result["etf_flows"] = pd.DataFrame()
        result["etf_market_agg"] = None

    # ── 6. Naver 상위 200개 상세 데이터 (재무 + 투자자 + PBR/배당) ──
    for market_name in ["kospi", "kosdaq"]:
        df = result.get(market_name, pd.DataFrame())
        if df.empty:
            continue

        top_tickers = df.head(200)["티커"].tolist()
        # 티커 → 현재가 매핑 (투자자 주수 → 억원 변환용)
        price_map: dict = {}
        if "현재가" in df.columns:
            price_map = dict(zip(df["티커"], df["현재가"].values))

        logger.info(f"Naver 상세 수집 ({market_name.upper()}, 상위 200개): PBR/배당/재무/투자자...")
        try:
            details_df = crawl_naver_stock_details(top_tickers, price_map, n=200, investor_days=biz_days)
            if details_df.empty:
                continue

            # 병합할 컬럼 목록
            detail_cols = ["티커"] + [
                c for c in [
                    "PER_NAVER", "PBR_NAVER", "배당수익률_NAVER",
                    "연간_매출액", "연간_영업이익", "영업이익률",
                    "연간_ROE", "부채비율",
                    "매출액_증가율", "영업이익_증가율",
                    "1주외국인매매", "1주기관매매",
                ]
                if c in details_df.columns
            ]
            df = df.merge(details_df[detail_cols], on="티커", how="left")

            # PER: 전종목 JSON 대량 조회에는 PER 이 없다 — 상세(상위 N)에서 채운다 (SJAIINV-197)
            if "PER_NAVER" in df.columns:
                df["PER"] = df["PER_NAVER"]
                df.drop(columns=["PER_NAVER"], inplace=True, errors="ignore")

            # PBR: Naver 우선 적용
            if "PBR_NAVER" in df.columns:
                df["PBR"] = df["PBR_NAVER"]
                df.drop(columns=["PBR_NAVER"], inplace=True, errors="ignore")

            # 배당수익률: Naver 우선 적용
            if "배당수익률_NAVER" in df.columns:
                df["배당수익률"] = df["배당수익률_NAVER"]
                df.drop(columns=["배당수익률_NAVER"], inplace=True, errors="ignore")

            result[market_name] = df
        except Exception as e:
            logger.error(f"Naver 상세 수집 실패 ({market_name}): {e}")

    result["base_date"] = base_str
    result["week_date"] = week_date_str
    result["week_start"] = week_start_str
    result["biz_days"] = biz_days
    result["is_midweek"] = is_midweek
    result["period_label"] = period_label

    logger.info("전체 데이터 수집 완료")
    return result
