"""SJAIINV-52: pykrx 를 쓰는 지점이 스스로 자격증명을 보증한다.

기존에는 main.py:141(run_pipeline) 한 곳에서만 주입해서, 그 경로를 거치지
않는 호출자(수동 발행 CLI)는 KRX 로그인 없이 돌았다 — 시간적 결합이다.
inject_credentials 는 멱등(env 있으면 즉시 True)이라 사용 지점마다 불러도
금요일 파이프라인에는 비용이 없다.
"""
import re
from pathlib import Path
from unittest import mock

import pandas as pd

from modules import crawler, publisher

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_crawl_krx_investor_flows_injects_credentials_first():
    calls = []
    with mock.patch.object(crawler.krx_auth, "inject_credentials",
                           side_effect=lambda: calls.append("inject") or True), \
         mock.patch("pykrx.stock.get_market_net_purchases_of_equities_by_ticker",
                    side_effect=lambda *a, **k: calls.append("pykrx") or pd.DataFrame()):
        try:
            crawler.crawl_krx_investor_flows("20260817", "20260821")
        except Exception:
            pass  # 빈 응답 → RuntimeError. 여기서 보는 건 호출 순서다
    assert calls[:2] == ["inject", "pykrx"]


def test_crawl_krx_etf_flows_injects_credentials_first():
    calls = []
    with mock.patch.object(crawler.krx_auth, "inject_credentials",
                           side_effect=lambda: calls.append("inject") or True), \
         mock.patch("pykrx.stock.get_etf_trading_volume_and_value",
                    side_effect=lambda *a, **k: calls.append("pykrx") or pd.DataFrame()), \
         mock.patch("pykrx.stock.get_etf_ticker_list", return_value=[]):
        try:
            crawler.crawl_krx_etf_flows("20260817", "20260821")
        except Exception:
            pass
    assert calls[0] == "inject"
    assert "pykrx" in calls


# ── 가드: 사람이 만든 목록은 새고, 전수 스캔은 안 샌다 ──

def _production_sources():
    skip = {".venv", "__pycache__", ".git", ".pytest_cache", "tests", ".superpowers"}
    for path in REPO_ROOT.rglob("*.py"):
        if skip.isdisjoint(path.relative_to(REPO_ROOT).parts):
            yield path


_PYKRX_IMPORT = re.compile(r"^\s*from pykrx import|^\s*import pykrx", re.M)


def test_every_pykrx_user_guarantees_credentials():
    """pykrx 를 import 하는 프로덕션 파일은 반드시 inject_credentials 를 참조한다."""
    # 가드가 실패할 수 있어야 가드다
    assert _PYKRX_IMPORT.search("    from pykrx import stock")
    assert not _PYKRX_IMPORT.search("# pykrx 는 로그인이 필요하다")

    offenders = [
        str(p.relative_to(REPO_ROOT))
        for p in _production_sources()
        if _PYKRX_IMPORT.search(p.read_text(encoding="utf-8"))
        and "inject_credentials" not in p.read_text(encoding="utf-8")
    ]
    assert offenders == []
