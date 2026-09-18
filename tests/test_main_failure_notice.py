"""SJAIINV-197: 파이프라인이 죽으면 DM 으로 알린다.

09-11·09-18 두 번의 실패는 수집 단계의 미처리 예외였다. 통지 코드가 발행 단계에만
있어서 파이프라인은 아무 말 없이 exit 1 로 끝났고, 트레이스백은 pipeline_boot.log
에만 남았다. 외부 감시 작업의 알림이 없었다면 더 오래 몰랐을 것이다.
"""
from unittest import mock

import pytest

import main

CFG = {"publish": {"notify_chat_id": "123"}}


def test_failure_sends_dm_and_reraises(caplog):
    boom = RuntimeError("네이버 증권 KOSPI 전종목 수집 0건")
    with mock.patch.object(main, "run_pipeline", side_effect=boom), \
         mock.patch("modules.notifier.send_message") as dm, \
         mock.patch.object(main.sys, "argv", ["main.py", "--run-now"]), \
         caplog.at_level("ERROR"):
        with pytest.raises(RuntimeError):
            main.run_with_failure_notice(CFG, midweek=False)
    dm.assert_called_once()
    chat_id, text = dm.call_args[0]
    assert chat_id == "123"
    assert "WST" in text and "실패" in text and "수집 0건" in text and "RuntimeError" in text
    # 트레이스백이 pipeline.log 에도 남아야 한다 (기존에는 boot 로그에만 있었다)
    assert any(r.exc_info for r in caplog.records)


def test_private_pdf_mode_never_sends_telegram():
    """--private-pdf 는 텔레그램 발송 전면 금지 모드다 — 실패 통지도 예외가 아니다."""
    with mock.patch.object(main, "run_pipeline", side_effect=RuntimeError("x")), \
         mock.patch("modules.notifier.send_message") as dm, \
         mock.patch.object(main.sys, "argv", ["main.py", "--run-now", "--private-pdf"]):
        with pytest.raises(RuntimeError):
            main.run_with_failure_notice(CFG, midweek=False)
    dm.assert_not_called()


def test_notify_failure_does_not_mask_the_original_error():
    with mock.patch.object(main, "run_pipeline", side_effect=KeyError("티커")), \
         mock.patch("modules.notifier.send_message", side_effect=RuntimeError("telegram down")), \
         mock.patch.object(main.sys, "argv", ["main.py", "--run-now"]):
        with pytest.raises(KeyError):
            main.run_with_failure_notice(CFG, midweek=False)


def test_success_sends_nothing():
    with mock.patch.object(main, "run_pipeline", return_value=None), \
         mock.patch("modules.notifier.send_message") as dm:
        main.run_with_failure_notice(CFG, midweek=False)
    dm.assert_not_called()
