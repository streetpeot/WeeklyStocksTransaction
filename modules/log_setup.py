"""로깅 설정 — 정의는 이 파일 한 곳 (SJAIINV-51).

시각은 UTC ISO-8601 밀리초(`...Z`)로 줄 앞에 붙는다. 기존 본문
(`[LEVEL] name: message`)은 그대로 두고 시각 표기만 바꾼다.
"""
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

UTC = timezone.utc


def _utc_from_timestamp(created: float) -> datetime:
    """LogRecord.created(epoch 초) → aware UTC datetime."""
    return datetime.fromtimestamp(created, UTC)


class ISO8601Formatter(logging.Formatter):
    """`2026-08-21T11:14:06.443Z [INFO] modules.publisher: ...`

    clock 은 `LogRecord.created` 를 aware datetime 으로 바꾸는 변환기다.
    테스트에서 고정값을 주입한다.
    """

    DEFAULT_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

    def __init__(self, fmt: str | None = None, clock=None):
        super().__init__(fmt or self.DEFAULT_FORMAT)
        self._clock = clock or _utc_from_timestamp

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        moment = self._clock(record.created)
        if moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
            raise ValueError(
                "log clock must return an aware datetime — naive 를 astimezone 하면 "
                "로컬(KST)로 해석돼 9시간 밀린 값이 조용히 찍힌다"
            )
        moment = moment.astimezone(UTC)
        return f"{moment:%Y-%m-%dT%H:%M:%S}.{moment.microsecond // 1000:03d}Z"


def setup_logging(log_file: str | Path | None = None, *,
                  level: int = logging.INFO, clock=None, stream=None) -> None:
    """진입점에서 한 번만 호출한다 — 이 리포에서 로깅을 설정하는 유일한 방법.

    `logging.basicConfig` 를 직접 부르지 말 것: 진입점마다 포맷이 갈려
    수동 실행 로그에만 시각이 빠지는 드리프트가 실제로 있었다(SJAIINV-51).
    import 시점이 아니라 `__main__` 에서 부른다 — 그래야 이 모듈을 import
    하는 테스트·검증 스크립트가 운영 로그를 오염시키지 않는다.
    """
    handlers: list[logging.Handler] = [logging.StreamHandler(stream or sys.stdout)]
    if log_file is not None:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))

    formatter = ISO8601Formatter(clock=clock)
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    for handler in handlers:
        handler.setFormatter(formatter)
        root.addHandler(handler)
    root.setLevel(level)
