import pytest
from unittest import mock

from modules import publisher


def test_week_range_normal_week():
    # KRX 폴백 계층 성공 경로: 월~금 5거래일을 흉내 (KIS 는 실패 주입)
    with mock.patch.object(publisher, "_kis_open_days", side_effect=RuntimeError("KIS down")), \
         mock.patch.object(publisher, "_krx_trading_days",
                           return_value=["20260706", "20260707", "20260708", "20260709", "20260710"]):
        assert publisher.compute_week_range("20260710") == "20260706~0710"


def test_week_range_holiday_week():
    with mock.patch.object(publisher, "_kis_open_days", side_effect=RuntimeError("KIS down")), \
         mock.patch.object(publisher, "_krx_trading_days",
                           return_value=["20260303", "20260304"]):
        assert publisher.compute_week_range("20260304") == "20260303~0304"


def test_week_range_single_day():
    with mock.patch.object(publisher, "_kis_open_days", side_effect=RuntimeError("KIS down")), \
         mock.patch.object(publisher, "_krx_trading_days", return_value=["20260710"]):
        assert publisher.compute_week_range("20260710") == "20260710"


def test_week_range_fallback_when_krx_fails():
    with mock.patch.object(publisher, "_kis_open_days", side_effect=RuntimeError("KIS down")), \
         mock.patch.object(publisher, "_krx_trading_days", side_effect=RuntimeError("KRX down")):
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


def test_publish_private_pdf_skips_send_and_writes_sentinel(tmp_path, monkeypatch):
    from modules import publisher

    # 리포트 md (파일명은 _YYYYMMDD.md 패턴 필수)
    report = tmp_path / "국내증시 자금동향_20260719.md"
    report.write_text("---\ntitle: x\n---\n# 본문\n", encoding="utf-8")

    config = {
        "publish": {
            "vault_ingest": "/ignored",
            "notify_chat_id": "988006216",
            "telegram_channel": "@somechannel",
            "pdf_enabled": True,
        },
        "output": {"chart_dir": str(tmp_path / "charts")},
    }

    # 볼트 반입 subprocess는 성공으로 mock (실제 vault_ingest 실행 안 함)
    from types import SimpleNamespace
    monkeypatch.setattr(publisher.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(returncode=0, stdout="반입 완료", stderr=""))

    fake_pdf = tmp_path / "pdf" / "out.pdf"
    fake_pdf.parent.mkdir(parents=True, exist_ok=True)
    fake_pdf.write_bytes(b"%PDF-fake")
    monkeypatch.setattr(publisher.pdf_export, "md_to_pdf", lambda md, out: fake_pdf)

    sent = []
    monkeypatch.setattr(publisher.notifier, "send_document",
                        lambda *a, **k: sent.append(a))

    sentinel = tmp_path / "data" / "last_private_pdf.txt"
    monkeypatch.setattr(publisher, "SENTINEL_PATH", sentinel)

    # 주간 범위는 이 테스트의 관심사가 아니다 — mock 하지 않으면 실제 KRX 를 친다
    monkeypatch.setattr(publisher, "_krx_trading_days",
                        lambda s, e: ["20260713", "20260719"])

    errors = publisher.publish(config, report, private_pdf=True)

    assert sent == []                                   # 전송 스킵
    assert sentinel.read_text(encoding="utf-8").strip() == str(fake_pdf)
    assert errors == []


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


def test_krx_trading_days_retries_transient_failure():
    """8/21·7/17 실측: pykrx가 비정상 응답에 KeyError('지수명')를 던진다 — 일시적이다.

    재시도가 없으면 달력 폴백으로 내려가 휴장일이 주간 범위에 섞인다
    (2026-08-17 광복절 대체공휴일이 실제로 그렇게 발행됐다).
    """
    import pandas as pd
    ok = pd.DataFrame(index=pd.to_datetime(["20260818", "20260821"]))
    with mock.patch.object(publisher, "_krx_ohlcv",
                           side_effect=[KeyError("지수명"), ok]) as call, \
         mock.patch.object(publisher.time, "sleep"):
        assert publisher._krx_trading_days("20260817", "20260821") == ["20260818", "20260821"]
        assert call.call_count == 2


def test_krx_trading_days_gives_up_after_retries():
    with mock.patch.object(publisher, "_krx_ohlcv", side_effect=KeyError("지수명")) as call, \
         mock.patch.object(publisher.time, "sleep"):
        with pytest.raises(KeyError):
            publisher._krx_trading_days("20260817", "20260821")
        assert call.call_count == 3


def test_calendar_fallback_warns_that_range_may_include_holidays(caplog):
    """폴백 결과는 추정이다 — 로그만 보고 제목을 신뢰하면 안 된다는 걸 남긴다."""
    with mock.patch.object(publisher, "_kis_open_days", side_effect=RuntimeError("KIS down")), \
         mock.patch.object(publisher, "_krx_trading_days",
                           side_effect=KeyError("지수명")), \
         caplog.at_level("WARNING"):
        publisher.compute_week_range("20260821")
    msg = " ".join(r.message for r in caplog.records)
    assert "휴장일" in msg and "추정" in msg


def test_krx_trading_days_injects_credentials_before_calling_pykrx():
    """수동 발행 CLI 는 run_pipeline 을 거치지 않아 주입 지점이 없었다 (SJAIINV-52)."""
    import pandas as pd
    ok = pd.DataFrame(index=pd.to_datetime(["20260818", "20260821"]))
    calls = []
    with mock.patch.object(publisher.krx_auth, "inject_credentials",
                           side_effect=lambda: calls.append("inject") or True), \
         mock.patch.object(publisher, "_krx_ohlcv",
                           side_effect=lambda *a: calls.append("pykrx") or ok):
        publisher._krx_trading_days("20260817", "20260821")
    assert calls == ["inject", "pykrx"]


def test_krx_trading_days_gives_up_immediately_when_credentials_missing():
    """자격증명이 없으면 3회 전부 확정 실패다 — 4.8초와 pykrx 트레이스백 3벌을 낭비하지 않는다."""
    with mock.patch.object(publisher.krx_auth, "inject_credentials", return_value=False), \
         mock.patch.object(publisher, "_krx_ohlcv") as ohlcv, \
         mock.patch.object(publisher.time, "sleep") as slept:
        with pytest.raises(RuntimeError, match="자격증명"):
            publisher._krx_trading_days("20260817", "20260821")
        assert ohlcv.call_count == 0
        assert slept.call_count == 0


def test_krx_trading_days_treats_empty_frame_as_failure_not_empty_result():
    """pykrx 는 로그인 실패를 삼키고 빈 DataFrame 을 돌려준다 — crawler 가 이미
    "빈 응답도 실패로 취급"하는 선례가 있다. 빈 결과를 그대로 통과시키면
    compute_week_range 가 days=[] → [base_date] 로 접어 **단일 날짜 제목**이
    경고 없이 발행된다 (SJAIINV-52)."""
    import pandas as pd
    empty = pd.DataFrame()
    with mock.patch.object(publisher.krx_auth, "inject_credentials", return_value=True), \
         mock.patch.object(publisher, "_krx_ohlcv", return_value=empty) as ohlcv, \
         mock.patch.object(publisher.time, "sleep"):
        with pytest.raises(RuntimeError, match="빈 응답"):
            publisher._krx_trading_days("20260817", "20260821")
        assert ohlcv.call_count == 3  # 일시적일 수 있으므로 재시도는 한다


def test_compute_week_range_does_not_collapse_to_single_date_on_empty_frame():
    """위 취약점의 사용자 관점 증상 — 제목이 '20260821' 한 날짜로 나가면 안 된다."""
    import pandas as pd
    with mock.patch.object(publisher, "_kis_open_days", side_effect=RuntimeError("KIS down")), \
         mock.patch.object(publisher.krx_auth, "inject_credentials", return_value=True), \
         mock.patch.object(publisher, "_krx_ohlcv", return_value=pd.DataFrame()), \
         mock.patch.object(publisher.time, "sleep"):
        assert publisher.compute_week_range("20260821") == "20260817~0821"  # 달력 폴백


# ── SJAIINV-63: 발행 단계 거래일 조회는 KIS 휴장일 API 가 1순위 ──
# KRX 지수 엔드포인트는 금요일 저녁(=발행 시각)에 죽는다 — 3회 실측(07-17·08-21·08-28).
# 같은 시각 KIS chk-holiday 는 정상임을 08-28 21:2x 에 라이브로 확인했다.

def _kis_resp(rows, rt_cd="0", msg=""):
    r = mock.Mock()
    r.json.return_value = {"rt_cd": rt_cd, "msg1": msg, "output": rows}
    return r


def test_kis_open_days_filters_opnd_yn_within_range():
    """⚠️ tr_day_yn 은 토요일도 'Y' 다(08-29 실측) — opnd_yn(개장일)만 믿는다."""
    rows = [
        {"bass_dt": "20260817", "opnd_yn": "N", "tr_day_yn": "Y"},  # 광복절 대체(실측)
        {"bass_dt": "20260818", "opnd_yn": "Y", "tr_day_yn": "Y"},
        {"bass_dt": "20260819", "opnd_yn": "Y", "tr_day_yn": "Y"},
        {"bass_dt": "20260820", "opnd_yn": "Y", "tr_day_yn": "Y"},
        {"bass_dt": "20260821", "opnd_yn": "Y", "tr_day_yn": "Y"},
        {"bass_dt": "20260822", "opnd_yn": "N", "tr_day_yn": "Y"},  # 토요일 함정
        {"bass_dt": "20260823", "opnd_yn": "N", "tr_day_yn": "Y"},
    ]
    with mock.patch.object(publisher, "_kis_headers", return_value={}), \
         mock.patch.object(publisher.requests, "get", return_value=_kis_resp(rows)):
        assert publisher._kis_open_days("20260817", "20260821") == \
            ["20260818", "20260819", "20260820", "20260821"]


def test_kis_open_days_raises_on_error_rt_cd():
    with mock.patch.object(publisher, "_kis_headers", return_value={}), \
         mock.patch.object(publisher.requests, "get",
                           return_value=_kis_resp([], rt_cd="1", msg="EGW00123")):
        with pytest.raises(RuntimeError, match="EGW00123"):
            publisher._kis_open_days("20260817", "20260821")


def test_kis_open_days_treats_empty_result_as_failure():
    """빈 응답 = 실패 — 리포 관례. 통과시키면 단일 날짜 제목으로 접힌다."""
    rows = [{"bass_dt": "20260829", "opnd_yn": "N", "tr_day_yn": "Y"}]  # 범위 밖뿐
    with mock.patch.object(publisher, "_kis_headers", return_value={}), \
         mock.patch.object(publisher.requests, "get", return_value=_kis_resp(rows)):
        with pytest.raises(RuntimeError, match="빈 응답"):
            publisher._kis_open_days("20260824", "20260828")


def test_compute_week_range_prefers_kis_and_skips_krx():
    with mock.patch.object(publisher, "_kis_open_days",
                           return_value=["20260818", "20260819", "20260820", "20260821"]), \
         mock.patch.object(publisher, "_krx_trading_days") as krx:
        assert publisher.compute_week_range("20260821") == "20260818~0821"
        krx.assert_not_called()


def test_compute_week_range_falls_back_to_krx_when_kis_fails():
    with mock.patch.object(publisher, "_kis_open_days", side_effect=RuntimeError("KIS down")), \
         mock.patch.object(publisher, "_krx_trading_days",
                           return_value=["20260818", "20260819", "20260820", "20260821"]):
        assert publisher.compute_week_range("20260821") == "20260818~0821"
