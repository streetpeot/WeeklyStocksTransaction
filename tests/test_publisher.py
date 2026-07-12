from unittest import mock

from modules import publisher


def test_week_range_normal_week():
    # pykrx 성공 경로: 월~금 5거래일을 흉내
    with mock.patch.object(publisher, "_krx_trading_days",
                           return_value=["20260706", "20260707", "20260708", "20260709", "20260710"]):
        assert publisher.compute_week_range("20260710") == "20260706~0710"


def test_week_range_holiday_week():
    with mock.patch.object(publisher, "_krx_trading_days",
                           return_value=["20260303", "20260304"]):
        assert publisher.compute_week_range("20260304") == "20260303~0304"


def test_week_range_single_day():
    with mock.patch.object(publisher, "_krx_trading_days", return_value=["20260710"]):
        assert publisher.compute_week_range("20260710") == "20260710"


def test_week_range_fallback_when_krx_fails():
    with mock.patch.object(publisher, "_krx_trading_days", side_effect=RuntimeError("KRX down")):
        # 2026-07-10 = 금 → 달력 폴백 월(0706)~기준일(0710)
        assert publisher.compute_week_range("20260710") == "20260706~0710"


def test_dest_name_for():
    with mock.patch.object(publisher, "compute_week_range", return_value="20260706~0710"):
        assert publisher.dest_name_for("20260710", "국내증시 자금동향") == "국내증시 자금동향_20260706~0710"


CFG = {"publish": {
    "vault_ingest": "/tmp/fake_ingest.py",
    "title_prefix": "국내증시 자금동향",
    "pdf_enabled": True,
    "telegram_channel": "-1004491335260",
    "notify_chat_id": "988006216",
}, "output": {"report_dir": "./data", "chart_dir": "./data/charts"}}


def _setup_report(tmp_path):
    (tmp_path / "charts").mkdir()
    md = tmp_path / "주가자금동향_20260710.md"
    md.write_text("---\ntitle: x\n---\n# r\n", encoding="utf-8")
    (tmp_path / "charts/flow_kospi_20260710.png").write_bytes(b"png")
    return md


def test_publish_happy_path(tmp_path):
    md = _setup_report(tmp_path)
    with mock.patch.object(publisher, "dest_name_for", return_value="국내증시 자금동향_20260706~0710"), \
         mock.patch.object(publisher.subprocess, "run", return_value=mock.Mock(returncode=0)) as ingest, \
         mock.patch.object(publisher.pdf_export, "md_to_pdf",
                           side_effect=lambda m, p: p) as pdf, \
         mock.patch.object(publisher.notifier, "send_document") as send, \
         mock.patch.object(publisher.notifier, "send_message") as dm:
        errs = publisher.publish(CFG, md)
    assert errs == []
    assert ingest.called and pdf.called
    assert send.call_args[0][0] == "-1004491335260"
    dm.assert_not_called()  # 성공 시 무음


def test_publish_ingest_lint_warning_notifies_but_continues(tmp_path):
    md = _setup_report(tmp_path)
    with mock.patch.object(publisher, "dest_name_for", return_value="국내증시 자금동향_20260706~0710"), \
         mock.patch.object(publisher.subprocess, "run", return_value=mock.Mock(returncode=2, stderr="lint")), \
         mock.patch.object(publisher.pdf_export, "md_to_pdf", side_effect=lambda m, p: p), \
         mock.patch.object(publisher.notifier, "send_document"), \
         mock.patch.object(publisher.notifier, "send_message") as dm:
        errs = publisher.publish(CFG, md)
    assert len(errs) == 1 and "lint" in errs[0]
    dm.assert_called_once()  # 경고 DM


def test_publish_pdf_failure_skips_send_keeps_ingest(tmp_path):
    md = _setup_report(tmp_path)
    with mock.patch.object(publisher, "dest_name_for", return_value="국내증시 자금동향_20260706~0710"), \
         mock.patch.object(publisher.subprocess, "run", return_value=mock.Mock(returncode=0)), \
         mock.patch.object(publisher.pdf_export, "md_to_pdf", side_effect=RuntimeError("chrome")), \
         mock.patch.object(publisher.notifier, "send_document") as send, \
         mock.patch.object(publisher.notifier, "send_message") as dm:
        errs = publisher.publish(CFG, md)
    assert any("PDF" in e for e in errs)
    send.assert_not_called()
    dm.assert_called_once()


def test_publish_dm_failure_swallowed(tmp_path):
    md = _setup_report(tmp_path)
    with mock.patch.object(publisher, "dest_name_for", return_value="국내증시 자금동향_20260706~0710"), \
         mock.patch.object(publisher.subprocess, "run", return_value=mock.Mock(returncode=1, stderr="fail")), \
         mock.patch.object(publisher.pdf_export, "md_to_pdf", side_effect=lambda m, p: p), \
         mock.patch.object(publisher.notifier, "send_document"), \
         mock.patch.object(publisher.notifier, "send_message", side_effect=RuntimeError("tg down")):
        errs = publisher.publish(CFG, md)  # 예외로 죽지 않아야 함
    assert len(errs) >= 1
