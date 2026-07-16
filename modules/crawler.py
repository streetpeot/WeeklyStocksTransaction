"""
crawler.py - 데이터 수집 모듈
■ 네이버금융 페이지 스크래핑: 전종목 시총/주가/PER/ROE/외국인비율
■ KIS REST API: 기관/외국인 순매수 상위 30, 지수, 개별 PBR·배당
■ 네이버금융 개별: 섹터(업종), 재무 정보 (비동기)
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

    # ── 기관/외국인 순매수 상위 ──
    def get_investor_rank(self, market: str = "J") -> list[dict]:
        """
        기관/외국인 순매수 상위 종목 (FHPST01770000)
        market: "J"=KOSPI, "Q"=KOSDAQ
        """
        try:
            d = self.get(
                "FHPST01770000",
                "/uapi/domestic-stock/v1/ranking/investor",
                {
                    "FID_COND_MRKT_DIV_CODE": market,
                    "FID_COND_SCR_DIV_CODE": "20177",
                    "FID_INPUT_ISCD": "0000",
                    "FID_DIV_CLS_CODE": "0",
                    "FID_BLNG_CLS_CODE": "0",
                    "FID_TRGT_CLS_CODE": "111111111",
                    "FID_TRGT_EXLS_CLS_CODE": "0000000000",
                    "FID_INPUT_PRICE_1": "0",
                    "FID_INPUT_PRICE_2": "0",
                    "FID_VOL_CNT": "0",
                    "FID_INPUT_DATE_1": "",
                },
            )
            if d.get("rt_cd") == "0":
                return d.get("output", [])
            logger.warning(f"KIS investor_rank 실패: {d.get('msg1')}")
        except Exception as e:
            logger.warning(f"KIS investor_rank 예외: {e}")

        # 폴백: 시장 전체 당일 기관/외국인 동향 (FHKST03900300)
        try:
            d2 = self.get(
                "FHKST03900300",
                "/uapi/domestic-stock/v1/quotations/inquire-investor",
                {"FID_COND_MRKT_DIV_CODE": market, "FID_INPUT_ISCD": "0000"},
            )
            if d2.get("rt_cd") == "0":
                return d2.get("output", [])
        except Exception as e2:
            logger.warning(f"KIS investor 폴백 실패: {e2}")
        return []

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
            "FHKUP03500200",
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

def _parse_naver_market_page(html: str) -> list[dict]:
    """네이버금융 시가총액 순위 페이지 파싱
    컬럼 구조: N | 종목명 | 현재가 | 전일비 | 등락률 | 액면가 | 시가총액 | 상장주식수 | 외국인비율 | 거래량 | PER | ROE
    """
    soup = BeautifulSoup(html, "lxml")
    rows = soup.select("table.type_2 tbody tr")
    records = []
    for row in rows:
        tds = row.find_all("td")
        if len(tds) < 12:
            continue
        # 순위
        rank_td = tds[0].get_text(strip=True)
        if not rank_td.isdigit():
            continue
        # 종목 링크에서 티커 추출
        link = row.find("a", href=True)
        ticker = ""
        if link:
            href = link.get("href", "")
            if "code=" in href:
                ticker = href.split("code=")[-1].split("&")[0].strip()
        name = tds[1].get_text(strip=True)

        def parse_num(td_idx: int) -> Optional[float]:
            t = tds[td_idx].get_text(strip=True).replace(",", "").replace("%", "")
            try:
                return float(t)
            except ValueError:
                return None

        cur_price = parse_num(2)
        trading_vol = parse_num(9)
        # 거래대금 = 거래량 × 현재가 / 1억 (근사값)
        trading_value = round(trading_vol * cur_price / 100_000_000, 2) if trading_vol and cur_price else None

        records.append({
            "순위": int(rank_td),
            "티커": ticker,
            "종목명": name,
            "현재가": cur_price,
            "전일대비": tds[3].get_text(strip=True),
            "등락률_당일": parse_num(4),
            "시가총액(억)": parse_num(6),
            "상장주식수": parse_num(7),
            "외국인비율(%)": parse_num(8),
            "거래량": trading_vol,
            "거래대금(억)": trading_value,
            "PER": parse_num(10),
            "ROE": parse_num(11),
        })
    return records


def crawl_naver_market(market_code: int, max_pages: Optional[int] = None) -> pd.DataFrame:
    """
    네이버금융 시장 전종목 시총 순위 크롤링
    market_code: 0=KOSPI, 1=KOSDAQ
    """
    base_url = "https://finance.naver.com/sise/sise_market_sum.nhn"
    all_records = []
    session = requests.Session()

    # 전체 페이지 수 확인
    r = session.get(base_url, params={"sosok": market_code, "page": 1},
                    headers=NAVER_HEADERS, timeout=10)
    soup = BeautifulSoup(r.text, "lxml")
    pager = soup.select(".pgRR a")
    if pager:
        href = pager[0].get("href", "")
        last_page = int(href.split("page=")[-1]) if "page=" in href else 1
    else:
        last_page = 1

    if max_pages:
        last_page = min(last_page, max_pages)

    logger.info(f"네이버금융 {'KOSPI' if market_code==0 else 'KOSDAQ'} 크롤링: {last_page}페이지")

    for page in range(1, last_page + 1):
        try:
            r = session.get(base_url, params={"sosok": market_code, "page": page},
                            headers=NAVER_HEADERS, timeout=10)
            records = _parse_naver_market_page(r.text)
            all_records.extend(records)
            if page % 10 == 0:
                logger.info(f"  페이지 {page}/{last_page} 완료 ({len(all_records)}개)")
            time.sleep(0.3)
        except Exception as e:
            logger.warning(f"  페이지 {page} 실패: {e}")

    df = pd.DataFrame(all_records)
    df = df[df["티커"].str.len() == 6].reset_index(drop=True)
    logger.info(f"네이버금융 전종목 완료: {len(df)}개")
    return df


# ─────────────────────────────────────────
# 네이버금융 업종 그룹 → 섹터 매핑
# ─────────────────────────────────────────

def build_sector_map() -> dict:
    """네이버금융 업종별 종목 페이지로 {ticker: sector_name} 매핑 빌드
    개별 4224 페이지 대신 ~80개 업종 페이지만 크롤링 (훨씬 빠름)
    """
    session = requests.Session()
    ticker_sector: dict = {}

    # 1. 업종 목록 수집
    try:
        r = session.get(
            "https://finance.naver.com/sise/sise_group.naver?type=upjong",
            headers=NAVER_HEADERS, timeout=10,
        )
        soup = BeautifulSoup(r.content, "lxml", from_encoding="euc-kr")
        sectors = []
        for a in soup.select("table.type_1 td a"):
            href = a.get("href", "")
            if "no=" in href:
                no = href.split("no=")[-1].split("&")[0]
                name = a.get_text(strip=True)
                if no and name:
                    sectors.append((no, name))
    except Exception as e:
        logger.error(f"업종 목록 수집 실패: {e}")
        return {}

    logger.info(f"업종 수: {len(sectors)}개, 업종별 종목 매핑 빌드 중...")

    # 2. 각 업종별 종목 수집
    for no, sector_name in sectors:
        try:
            r2 = session.get(
                f"https://finance.naver.com/sise/sise_group_detail.naver?type=upjong&no={no}",
                headers=NAVER_HEADERS, timeout=10,
            )
            soup2 = BeautifulSoup(r2.content, "lxml", from_encoding="euc-kr")
            seen: set = set()
            for a in soup2.find_all("a", href=True):
                href = a.get("href", "")
                if "code=" in href:
                    ticker = href.split("code=")[-1].split("&")[0]
                    if len(ticker) == 6 and ticker not in seen:
                        ticker_sector[ticker] = sector_name
                        seen.add(ticker)
            time.sleep(0.2)
        except Exception as e:
            logger.warning(f"업종 {sector_name}({no}) 크롤링 실패: {e}")

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

def collect_kis_investor_rank(kis: KISClient) -> dict:
    """KIS API로 기관/외국인 순매수 상위 종목 수집"""
    result = {}
    market_map = {"KOSPI": "J", "KOSDAQ": "Q"}

    for market_name, market_code in market_map.items():
        try:
            ranks = kis.get_investor_rank(market_code)
            if ranks:
                # 컬럼명 매핑
                rows = []
                for r in ranks:
                    rows.append({
                        "티커": r.get("mksc_shrn_iscd", ""),
                        "종목명": r.get("hts_kor_isnm", ""),
                        "기관순매수": _safe_float(r.get("orgn_ntby_qty")),    # 기관 순매수량
                        "외국인순매수": _safe_float(r.get("frgn_ntby_qty")), # 외국인 순매수량
                        "기관순매수금액": _safe_float(r.get("orgn_ntby_tr_pbmn")),  # 억 단위 아닐 수 있음
                        "외국인순매수금액": _safe_float(r.get("frgn_ntby_tr_pbmn")),
                    })
                result[market_name] = pd.DataFrame(rows)
                logger.info(f"KIS 투자자 순위 ({market_name}): {len(rows)}개")
            time.sleep(0.5)
        except Exception as e:
            logger.error(f"KIS 투자자 순위 수집 실패 ({market_name}): {e}")
            result[market_name] = pd.DataFrame()

    return result


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

def _parse_naver_main_page(content: bytes, ticker: str, cur_price: Optional[float] = None, investor_days: int = 5) -> dict:
    """
    네이버금융 main.naver 페이지 파싱 (EUC-KR raw bytes 입력)
    수집 항목:
    - per_table: PBR, 배당수익률
    - tb_type1_ifrs: 연간 재무실적 (매출액/영업이익/ROE/부채비율/영업이익률/증가율)
    - tb_type1 (외국인/기관): 최근 investor_days일 순매수 합산 → 1주 기관/외국인 매매(억)
    """
    # main.naver 페이지는 UTF-8; BeautifulSoup이 자동 감지
    soup = BeautifulSoup(content, "lxml")
    result: dict = {"티커": ticker}

    # ── per_table: PBR, 배당수익률 ──
    per_table = soup.find("table", class_="per_table")
    if per_table:
        pbr_em = per_table.find("em", id="_pbr")
        if pbr_em:
            result["PBR_NAVER"] = _safe_float(pbr_em.get_text(strip=True))

        # 배당수익률: tr 단위로 검색 (tbody 구조에 무관하게)
        for tr in per_table.find_all("tr"):
            th = tr.find("th")
            if th and "배당" in th.get_text():
                td = tr.find("td")
                if td:
                    txt = td.get_text(strip=True).replace("%", "").replace(",", "").strip()
                    result["배당수익률_NAVER"] = _safe_float(txt)
                break

    # ── 기업실적분석 테이블: 연간 재무실적 ──
    fin_table = None
    for t in soup.find_all("table"):
        cls = " ".join(t.get("class", []))
        if "tb_type1_ifrs" in cls:
            fin_table = t
            break

    if fin_table:
        fin_data: dict = {}
        for tr in fin_table.find_all("tr"):
            th = tr.find("th")
            if not th:
                continue
            label = th.get_text(strip=True)
            vals = []
            for td in tr.find_all("td"):
                txt = td.get_text(strip=True).replace(",", "")
                try:
                    vals.append(float(txt))
                except (ValueError, TypeError):
                    vals.append(None)
            if vals:
                fin_data[label] = vals

        def get_annual(key: str, idx: int = 2) -> Optional[float]:
            v = fin_data.get(key, [])
            return v[idx] if len(v) > idx else None

        # ROE 행 레이블 동적 탐색 (ROE로 시작하는 첫 번째 키)
        roe_key = next((k for k in fin_data if k.startswith("ROE")), None)

        매출액_curr = get_annual("매출액", 2)
        매출액_prev = get_annual("매출액", 1)
        영업이익_curr = get_annual("영업이익", 2)
        영업이익_prev = get_annual("영업이익", 1)

        result["연간_매출액"]    = 매출액_curr
        result["연간_영업이익"]  = 영업이익_curr
        result["영업이익률"]     = get_annual("영업이익률", 2)
        result["연간_ROE"]       = get_annual(roe_key, 2) if roe_key else None
        result["부채비율"]       = get_annual("부채비율", 2)

        # 매출/영업이익 증가율 (최근 연간 vs 전년)
        if 매출액_curr and 매출액_prev and 매출액_prev != 0:
            result["매출액_증가율"] = round(
                (매출액_curr - 매출액_prev) / abs(매출액_prev) * 100, 2
            )
        if 영업이익_curr and 영업이익_prev and 영업이익_prev != 0:
            result["영업이익_증가율"] = round(
                (영업이익_curr - 영업이익_prev) / abs(영업이익_prev) * 100, 2
            )

    # ── 투자자별 매매동향: 최근 investor_days일 외국인/기관 합산 ──
    for tbl in soup.find_all("table"):
        cls = " ".join(tbl.get("class", []))
        summary = tbl.get("summary", "")
        if "tb_type1" not in cls:
            continue
        if "외국인" not in summary or "기관" not in summary:
            continue

        fore_sum = inst_sum = 0.0
        count = 0
        for tr in tbl.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 2:
                continue
            try:
                # 마지막 두 열: 외국인, 기관 (단위: 주)
                fore_v = float(tds[-2].get_text(strip=True).replace(",", "").replace("+", ""))
                inst_v = float(tds[-1].get_text(strip=True).replace(",", "").replace("+", ""))
                fore_sum += fore_v
                inst_sum += inst_v
                count += 1
                if count >= investor_days:
                    break
            except (ValueError, IndexError):
                pass

        if count > 0 and cur_price and cur_price > 0:
            # 주(株) × 현재가 / 1억 = 억원
            result["1주외국인매매"] = round(fore_sum * cur_price / 100_000_000, 2)
            result["1주기관매매"]   = round(inst_sum * cur_price / 100_000_000, 2)
        break  # 첫 번째 매칭 테이블만 사용

    return result


def crawl_naver_stock_details(
    tickers: list,
    prices: dict,
    n: int = 200,
    delay: float = 0.35,
    investor_days: int = 5,
) -> pd.DataFrame:
    """
    상위 N개 종목의 Naver 상세 데이터 수집 (main.naver)
    prices: {ticker: current_price} - 투자자 주수 → 억원 변환용
    investor_days: 투자자 매매 합산 거래일 수 (중간분석 시 이번 주 거래일 수)
    수집 항목: PBR, 배당수익률, 연간 재무실적, 1주 기관/외국인 매매
    """
    session = requests.Session()
    results = []
    tickers = tickers[:n]

    for i, ticker in enumerate(tickers):
        cur_price = prices.get(ticker)
        try:
            r = session.get(
                f"https://finance.naver.com/item/main.naver?code={ticker}",
                headers=NAVER_HEADERS,
                timeout=10,
            )
            row = _parse_naver_main_page(r.content, ticker, cur_price, investor_days=investor_days)
            results.append(row)
            if (i + 1) % 50 == 0:
                logger.info(f"Naver 상세 수집: {i+1}/{len(tickers)}")
            time.sleep(delay)
        except Exception as e:
            logger.debug(f"Naver 상세 수집 실패 ({ticker}): {e}")
            results.append({"티커": ticker})

    logger.info(f"Naver 상세 수집 완료: {len(results)}개")
    return pd.DataFrame(results) if results else pd.DataFrame()


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


# ─────────────────────────────────────────
# KRX ETF 투자자 순매수 (시장 집계 1콜 + 개별 ETF 루프)
# ─────────────────────────────────────────

def crawl_krx_etf_flows(fromdate: str, todate: str, top_n: int = 120, time_budget: float = 300.0):
    """KRX ETF 투자자 순매수 — 시장 전체 집계(1콜) + 거래대금 상위 top_n ETF 순매수.

    Returns: (etf_flows[티커,종목명,1주기관매매,1주외국인매매], etf_market_agg{외국인,기관,개인}|None)
    best-effort: 개별 티커 예외 스킵, time_budget(초) 초과 시 루프 중단, 집계 실패 시 None.
    거래대금 기준(억원). ETF 미거래 종목은 행 없음.
    KRX rate-limit(세션당 ~200콜) 회피 위해 전량이 아닌 거래대금 상위 top_n(기본 120)만 순회.
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

    # 개별 ETF 루프 — 거래대금 상위 top_n만 (KRX rate-limit[~200콜/세션] 회피)
    stock.get_etf_ticker_list(todate)  # 이름 캐시 워밍 (get_etf_ticker_name 로컬화)
    try:
        pc = stock.get_etf_price_change_by_ticker(fromdate, todate)
        tickers = list(pc.sort_values("거래대금", ascending=False).head(top_n).index)
    except Exception as e:
        logger.warning(f"ETF 거래대금 랭킹 실패({e}) → 이름목록 상위 {top_n}")
        tickers = stock.get_etf_ticker_list(todate)[:top_n]
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

    # ── 3. KIS API: 기관/외국인 순매수 상위 30 ──
    logger.info("KIS API 기관/외국인 순매수 상위 종목 수집...")
    try:
        investor_ranks = collect_kis_investor_rank(kis)
        result["investor_ranks"] = investor_ranks
    except Exception as e:
        logger.error(f"KIS 투자자 순위 수집 실패: {e}")
        result["investor_ranks"] = {}

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

    # ── 5.5 KRX 전종목 투자자 순매수 (기관·외국인, 주 4콜) ──
    monday_str = week_start_dt.strftime("%Y%m%d")
    try:
        result["krx_flows"] = crawl_krx_investor_flows(monday_str, base_str)
        result["flow_source"] = "krx"
    except Exception as e:
        logger.warning(f"KRX 전종목 수급 실패({e}) → Naver 상위200 폴백")
        result["krx_flows"] = pd.DataFrame()
        result["flow_source"] = "naver"

    # ── 5.6 KRX ETF 수급 (시장 집계 + 개별 ETF, best-effort) ──
    try:
        etf_flows, etf_agg = crawl_krx_etf_flows(monday_str, base_str)
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
                    "PBR_NAVER", "배당수익률_NAVER",
                    "연간_매출액", "연간_영업이익", "영업이익률",
                    "연간_ROE", "부채비율",
                    "매출액_증가율", "영업이익_증가율",
                    "1주외국인매매", "1주기관매매",
                ]
                if c in details_df.columns
            ]
            df = df.merge(details_df[detail_cols], on="티커", how="left")

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
