"""SJAIINV-199: 이 리포는 KRX 를 스크래핑하지 않는다.

KRX 는 2026-09-19 에 이 머신의 IP 를 "자동화 수단을 통한 비정상 대량 조회"로 1일간
제한했다. 이용약관 제10조 제2호가 자동화 수집을 금지하고, 재탐지 시 제한이 재적용된다.
탐지 직전에 보낸 것은 로그인 1회 + 조회 5~7콜뿐이었다 — 호출을 줄여서 피할 수 있는
문제가 아니다. 수급 데이터는 KIS 공식 API 로 옮겼다.

pykrx 는 **import 시점에** KRX 로그인을 시도한다. 그래서 호출이 아니라 import 를 막는다.
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_PYKRX_IMPORT = re.compile(r"^\s*(?:from|import)\s+pykrx\b", re.M)
_KRX_HOST = re.compile(r"data\.krx\.co\.kr")


def _production_sources():
    skip = {".venv", "__pycache__", ".git", ".pytest_cache", "tests", ".superpowers"}
    for path in REPO_ROOT.rglob("*.py"):
        if skip.isdisjoint(path.relative_to(REPO_ROOT).parts):
            yield path


def test_no_production_file_imports_pykrx_or_calls_krx_host():
    # 가드가 실패할 수 있어야 가드다
    assert _PYKRX_IMPORT.search("    from pykrx import stock")
    assert _PYKRX_IMPORT.search("import pykrx")
    assert not _PYKRX_IMPORT.search("# pykrx 는 import 시점에 로그인한다")
    assert _KRX_HOST.search('requests.post("https://data.krx.co.kr/comm/...")')

    offenders = []
    for p in _production_sources():
        src = p.read_text(encoding="utf-8")
        if _PYKRX_IMPORT.search(src) or _KRX_HOST.search(src):
            offenders.append(str(p.relative_to(REPO_ROOT)))
    assert offenders == []


def test_requirements_do_not_install_pykrx():
    reqs = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert not re.search(r"^\s*pykrx\b", reqs, re.M)
