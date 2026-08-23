"""SJAIINV-51: 로그를 UTC ISO-8601 로 정렬하고 로깅 설정을 한 곳으로 모은다.

규격(SJAIINV-46 내용이 -48 PR 에 실려 확정): UTC ISO-8601 · 줄 앞 · 정의는 한 곳 ·
시계 주입으로 테스트 고정 가능 · 기존 본문 보존. naive datetime 은 거부한다 —
astimezone 이 로컬로 해석해 KST 머신에서 9시간 밀린 값이 조용히 찍힌다.

WST 는 같은 초에 최대 18줄이 찍히므로 밀리초를 유지한다(있던 정보를 잃지 않는다).
"""
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from modules import log_setup

UTC = timezone.utc


def _record(msg: str = "발행 완료", name: str = "modules.publisher",
            level: int = logging.INFO) -> logging.LogRecord:
    return logging.LogRecord(name, level, "/x.py", 1, msg, None, None)


def test_formats_timestamp_as_utc_iso8601_with_milliseconds():
    fixed = datetime(2026, 8, 21, 11, 14, 6, 443000, tzinfo=UTC)
    fmt = log_setup.ISO8601Formatter(clock=lambda created: fixed)
    assert fmt.format(_record()) == (
        "2026-08-21T11:14:06.443Z [INFO] modules.publisher: 발행 완료"
    )


def test_rejects_naive_clock_instead_of_silently_shifting():
    """naive 를 astimezone 하면 로컬(KST)로 해석돼 9시간 밀린 값이 조용히 찍힌다.
    시각을 붙이는 목적이 「언제」를 밝히는 것이므로 틀린 값은 없느니만 못하다."""
    naive = datetime(2026, 8, 21, 20, 14, 6, 443000)  # tzinfo 없음
    fmt = log_setup.ISO8601Formatter(clock=lambda created: naive)
    with pytest.raises(ValueError, match="aware"):
        fmt.format(_record())


def test_rejects_clock_whose_tzinfo_yields_no_offset():
    """tzinfo 는 있는데 utcoffset()이 None 인 경우도 aware 가 아니다."""
    class _NoOffset(timezone.__base__):  # datetime.tzinfo
        def utcoffset(self, dt):
            return None

        def tzname(self, dt):
            return "X"

        def dst(self, dt):
            return None

    moment = datetime(2026, 8, 21, 20, 14, 6, tzinfo=_NoOffset())
    fmt = log_setup.ISO8601Formatter(clock=lambda created: moment)
    with pytest.raises(ValueError, match="aware"):
        fmt.format(_record())


def _root_snapshot():
    root = logging.getLogger()
    return list(root.handlers), root.level


def _root_restore(snapshot):
    handlers, level = snapshot
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    for h in handlers:
        root.addHandler(h)
    root.setLevel(level)


def test_setup_logging_installs_iso_formatter_on_console_only_by_default():
    snap = _root_snapshot()
    try:
        log_setup.setup_logging()
        root = logging.getLogger()
        assert root.level == logging.INFO
        assert len(root.handlers) == 1
        assert not isinstance(root.handlers[0], logging.FileHandler)
        assert isinstance(root.handlers[0].formatter, log_setup.ISO8601Formatter)
    finally:
        _root_restore(snap)


def test_setup_logging_adds_file_handler_when_asked(tmp_path):
    snap = _root_snapshot()
    log_file = tmp_path / "pipeline.log"
    try:
        fixed = datetime(2026, 8, 28, 11, 0, 1, 25000, tzinfo=UTC)
        log_setup.setup_logging(log_file, clock=lambda created: fixed)
        logging.getLogger("modules.crawler").info("수집 시작")
        for h in logging.getLogger().handlers:
            h.flush()
    finally:
        _root_restore(snap)
    assert log_file.read_text(encoding="utf-8").splitlines()[0] == (
        "2026-08-28T11:00:01.025Z [INFO] modules.crawler: 수집 시작"
    )


def test_setup_logging_replaces_previous_handlers_so_it_is_idempotent():
    snap = _root_snapshot()
    try:
        log_setup.setup_logging()
        log_setup.setup_logging()
        assert len(logging.getLogger().handlers) == 1
    finally:
        _root_restore(snap)


# ── 가드: 사람이 만든 목록은 새고, 전수 스캔은 안 샌다 ──
# MacroTracker 의 같은 가드가 사람 목록의 누락 7곳을 잡은 선례가 있다.

REPO_ROOT = Path(__file__).resolve().parent.parent


def _production_sources():
    """리포의 프로덕션 .py 전수 (가상환경·캐시·테스트 제외)."""
    skip = {".venv", "__pycache__", ".git", ".pytest_cache", "tests", ".superpowers"}
    for path in REPO_ROOT.rglob("*.py"):
        if skip.isdisjoint(path.relative_to(REPO_ROOT).parts):
            yield path


# 언급이 아니라 호출만 잡는다 — `logging.basicConfig(` 와 `basicConfig(` 둘 다.
_BASIC_CONFIG_CALL = re.compile(r"\bbasicConfig\s*\(")


def test_no_production_file_calls_basicConfig_directly():
    """설정은 log_setup 한 곳에서만 — 진입점마다 갈리면 드리프트가 난다."""
    # 가드가 실패할 수 있어야 가드다 (통과만 하는 가드는 아무것도 안 지킨다)
    assert _BASIC_CONFIG_CALL.search("logging.basicConfig(level=logging.INFO)")
    assert _BASIC_CONFIG_CALL.search("    basicConfig(format='x')")
    assert not _BASIC_CONFIG_CALL.search("`logging.basicConfig` 를 부르지 말 것")

    offenders = [
        str(p.relative_to(REPO_ROOT))
        for p in _production_sources()
        if _BASIC_CONFIG_CALL.search(p.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_every_logging_entry_point_calls_setup_logging():
    """`__main__` 을 가지면서 logging 을 쓰는 파일은 반드시 setup_logging 을 부른다.

    print 만 쓰는 진단 스크립트(scripts/verify_krx.py)는 대상이 아니다.
    """
    offenders = []
    for path in _production_sources():
        src = path.read_text(encoding="utf-8")
        if '__name__ == "__main__"' not in src:
            continue
        if "logging.getLogger" not in src:
            continue
        if "setup_logging" not in src:
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == []
