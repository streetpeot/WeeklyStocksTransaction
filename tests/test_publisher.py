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
         mock.patch.object(publisher.subprocess, "run",
                           return_value=mock.Mock(returncode=2, stdout="반입 완료: ...", stderr="lint")), \
         mock.patch.object(publisher.pdf_export, "md_to_pdf", side_effect=lambda m, p: p) as pdf, \
         mock.patch.object(publisher.notifier, "send_document") as send, \
         mock.patch.object(publisher.notifier, "send_message") as dm:
        errs = publisher.publish(CFG, md)
    assert len(errs) == 1 and "lint" in errs[0]
    dm.assert_called_once()  # 경고 DM
    assert pdf.called and send.called  # 파이프라인이 계속되었음


def test_publish_rc2_without_success_marker_is_failure(tmp_path):
    md = _setup_report(tmp_path)
    with mock.patch.object(publisher, "dest_name_for", return_value="국내증시 자금동향_20260706~0710"), \
         mock.patch.object(publisher.subprocess, "run",
                           return_value=mock.Mock(returncode=2, stdout="", stderr="can't open file")), \
         mock.patch.object(publisher.pdf_export, "md_to_pdf", side_effect=lambda m, p: p), \
         mock.patch.object(publisher.notifier, "send_document"), \
         mock.patch.object(publisher.notifier, "send_message"):
        errs = publisher.publish(CFG, md)
    assert any("반입 실패" in e for e in errs)


def test_publish_unknown_rc_is_failure(tmp_path):
    md = _setup_report(tmp_path)
    with mock.patch.object(publisher, "dest_name_for", return_value="국내증시 자금동향_20260706~0710"), \
         mock.patch.object(publisher.subprocess, "run",
                           return_value=mock.Mock(returncode=-9, stdout="", stderr="killed")), \
         mock.patch.object(publisher.pdf_export, "md_to_pdf", side_effect=lambda m, p: p), \
         mock.patch.object(publisher.notifier, "send_document"), \
         mock.patch.object(publisher.notifier, "send_message"):
        errs = publisher.publish(CFG, md)
    assert any("반입 실패" in e for e in errs)


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


def test_publish_ingest_launch_exception_counts_as_failure(tmp_path):
    md = _setup_report(tmp_path)
    with mock.patch.object(publisher, "dest_name_for", return_value="국내증시 자금동향_20260706~0710"), \
         mock.patch.object(publisher.subprocess, "run", side_effect=OSError("ingest 스크립트 없음")), \
         mock.patch.object(publisher.pdf_export, "md_to_pdf", side_effect=lambda m, p: p), \
         mock.patch.object(publisher.notifier, "send_document"), \
         mock.patch.object(publisher.notifier, "send_message"):
        errs = publisher.publish(CFG, md)
    assert any("실패" in e for e in errs)  # _main이 exit 1로 처리할 수 있어야 함


def test_publish_send_failure_notifies_dm(tmp_path):
    md = _setup_report(tmp_path)
    with mock.patch.object(publisher, "dest_name_for", return_value="국내증시 자금동향_20260706~0710"), \
         mock.patch.object(publisher.subprocess, "run", return_value=mock.Mock(returncode=0)), \
         mock.patch.object(publisher.pdf_export, "md_to_pdf", side_effect=lambda m, p: p), \
         mock.patch.object(publisher.notifier, "send_document", side_effect=RuntimeError("tg 500")) as send, \
         mock.patch.object(publisher.notifier, "send_message") as dm:
        errs = publisher.publish(CFG, md)
    assert any("전송 실패" in e for e in errs)
    dm.assert_called_once()


def test_publish_to_dm_routes_document_to_notify_chat(tmp_path):
    md = _setup_report(tmp_path)
    with mock.patch.object(publisher, "dest_name_for", return_value="국내증시 자금동향_20260706~0710"), \
         mock.patch.object(publisher.subprocess, "run", return_value=mock.Mock(returncode=0)), \
         mock.patch.object(publisher.pdf_export, "md_to_pdf", side_effect=lambda m, p: p), \
         mock.patch.object(publisher.notifier, "send_document") as send, \
         mock.patch.object(publisher.notifier, "send_message"):
        publisher.publish(CFG, md, to_dm=True)
    assert send.call_args[0][0] == "988006216"


def test_publish_personal_routes_vault_and_dm(tmp_path):
    """개인용 있으면: 볼트=개인용, 채널=공유용 PDF, DM=개인용 PDF."""
    md = _setup_report(tmp_path)
    personal = tmp_path / "주가자금동향_개인_20260710.md"
    personal.write_text("---\ntitle: x\n---\n# r\n## 워치리스트 수급\n", encoding="utf-8")
    with mock.patch.object(publisher, "dest_name_for", return_value="국내증시 자금동향_20260706~0710"), \
         mock.patch.object(publisher.subprocess, "run",
                           return_value=mock.Mock(returncode=0, stdout="반입 완료", stderr="")) as ingest, \
         mock.patch.object(publisher.pdf_export, "md_to_pdf", side_effect=lambda m, p: p) as pdf, \
         mock.patch.object(publisher.notifier, "send_document") as send, \
         mock.patch.object(publisher.notifier, "send_message") as dm:
        errs = publisher.publish(CFG, md, personal_path=personal)
    assert errs == []
    # 볼트 반입 = 개인용
    ingest_cmd = ingest.call_args[0][0]
    assert str(personal) in ingest_cmd
    assert str(md) not in ingest_cmd
    # 채널=공유용 PDF, DM=개인용 PDF (둘 다 전송)
    assert send.call_count == 2
    calls = {c[0][0]: c for c in send.call_args_list}
    assert "-1004491335260" in calls  # telegram_channel
    assert "988006216" in calls  # notify_chat_id
    dm_call = calls["988006216"]
    assert "_개인" in str(dm_call[0][1])
    assert dm_call[1]["caption"] == "국내증시 자금동향_20260706~0710 · 워치리스트"
    assert pdf.call_count == 2
    dm.assert_not_called()  # 경고 통지는 없음(별개 경로)


def test_publish_without_personal_keeps_current_behavior(tmp_path):
    """personal_path=None이면 현행과 동일: 볼트=공유용, 채널만 전송, DM 없음."""
    md = _setup_report(tmp_path)
    with mock.patch.object(publisher, "dest_name_for", return_value="국내증시 자금동향_20260706~0710"), \
         mock.patch.object(publisher.subprocess, "run",
                           return_value=mock.Mock(returncode=0, stdout="반입 완료", stderr="")) as ingest, \
         mock.patch.object(publisher.pdf_export, "md_to_pdf", side_effect=lambda m, p: p) as pdf, \
         mock.patch.object(publisher.notifier, "send_document") as send, \
         mock.patch.object(publisher.notifier, "send_message") as dm:
        errs = publisher.publish(CFG, md)
    assert errs == []
    ingest_cmd = ingest.call_args[0][0]
    assert str(md) in ingest_cmd
    assert send.call_count == 1
    assert send.call_args[0][0] == "-1004491335260"
    assert pdf.call_count == 1
    dm.assert_not_called()


def test_publish_pdf_disabled_skips_pdf_and_send(tmp_path):
    md = _setup_report(tmp_path)
    cfg = {**CFG, "publish": {**CFG["publish"], "pdf_enabled": False}}
    with mock.patch.object(publisher, "dest_name_for", return_value="국내증시 자금동향_20260706~0710"), \
         mock.patch.object(publisher.subprocess, "run", return_value=mock.Mock(returncode=0, stdout="반입 완료", stderr="")), \
         mock.patch.object(publisher.pdf_export, "md_to_pdf") as pdf, \
         mock.patch.object(publisher.notifier, "send_document") as send, \
         mock.patch.object(publisher.notifier, "send_message") as dm:
        errs = publisher.publish(cfg, md)
    assert errs == []
    pdf.assert_not_called(); send.assert_not_called(); dm.assert_not_called()


def test_publish_pdf_disabled_skips_personal_dm_too(tmp_path):
    """pdf_enabled=False면 personal_path가 있어도 개인용 DM 전송을 생략한다."""
    md = _setup_report(tmp_path)
    personal = tmp_path / "주가자금동향_개인_20260710.md"
    personal.write_text("---\ntitle: x\n---\n# r\n## 워치리스트 수급\n", encoding="utf-8")
    cfg = {**CFG, "publish": {**CFG["publish"], "pdf_enabled": False}}
    with mock.patch.object(publisher, "dest_name_for", return_value="국내증시 자금동향_20260706~0710"), \
         mock.patch.object(publisher.subprocess, "run",
                           return_value=mock.Mock(returncode=0, stdout="반입 완료", stderr="")) as ingest, \
         mock.patch.object(publisher.pdf_export, "md_to_pdf") as pdf, \
         mock.patch.object(publisher.notifier, "send_document") as send, \
         mock.patch.object(publisher.notifier, "send_message") as dm:
        errs = publisher.publish(cfg, md, personal_path=personal)
    assert errs == []
    # 볼트 반입만은 여전히 개인용 라우팅
    assert str(personal) in ingest.call_args[0][0]
    pdf.assert_not_called(); send.assert_not_called(); dm.assert_not_called()
