from pathlib import Path
from unittest import mock

import pytest

from modules import notifier


def _resp(ok=True):
    r = mock.Mock()
    r.status_code = 200 if ok else 500
    r.json.return_value = {"ok": ok}
    return r


def test_get_bot_token_reads_keychain():
    with mock.patch.object(notifier.subprocess, "run",
                           return_value=mock.Mock(returncode=0, stdout="TOKEN123\n")) as run:
        assert notifier.get_bot_token() == "TOKEN123"
        args = run.call_args[0][0]
        assert args[:2] == ["security", "find-generic-password"]


def test_get_bot_token_raises_when_missing():
    with mock.patch.object(notifier.subprocess, "run",
                           return_value=mock.Mock(returncode=44, stdout="")):
        with pytest.raises(RuntimeError):
            notifier.get_bot_token()


def test_send_document_posts_file(tmp_path: Path):
    f = tmp_path / "r.pdf"
    f.write_bytes(b"%PDF- fake")
    with mock.patch.object(notifier, "get_bot_token", return_value="T"), \
         mock.patch.object(notifier.requests, "post", return_value=_resp()) as post:
        notifier.send_document("-100123", f, caption="캡션")
        url = post.call_args[0][0]
        assert "botT/sendDocument" in url
        assert post.call_args[1]["data"]["chat_id"] == "-100123"


def test_send_document_retries_once_then_raises(tmp_path: Path):
    f = tmp_path / "r.pdf"
    f.write_bytes(b"x")
    with mock.patch.object(notifier, "get_bot_token", return_value="T"), \
         mock.patch.object(notifier.time, "sleep"), \
         mock.patch.object(notifier.requests, "post", return_value=_resp(ok=False)) as post:
        with pytest.raises(RuntimeError):
            notifier.send_document("-100123", f, caption="c")
        assert post.call_count == 2


def test_post_redacts_token_on_network_error():
    with mock.patch.object(notifier, "get_bot_token", return_value="SECRETTOKEN123"), \
         mock.patch.object(notifier.time, "sleep"), \
         mock.patch.object(notifier.requests, "post",
                           side_effect=notifier.requests.RequestException(
                               "Max retries exceeded with url: /botSECRETTOKEN123/sendMessage")):
        with pytest.raises(RuntimeError) as ei:
            notifier.send_message("123", "x")
        assert "SECRETTOKEN123" not in str(ei.value)
        assert "***" in str(ei.value)
