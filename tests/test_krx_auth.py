import os
import subprocess
from unittest.mock import patch, MagicMock

import pytest

from modules import krx_auth


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("KRX_ID", raising=False)
    monkeypatch.delenv("KRX_PW", raising=False)


def _mock_security(acct_stdout, pw_stdout):
    def side_effect(cmd, **kwargs):
        m = MagicMock()
        m.returncode = 0
        m.stdout = pw_stdout if "-w" in cmd else acct_stdout
        m.stderr = ""
        return m
    return side_effect


def test_inject_success(monkeypatch):
    acct_out = 'keychain: ...\n    "acct"<blob>="myid"\n'
    with patch("subprocess.run", side_effect=_mock_security(acct_out, "mypw\n")):
        assert krx_auth.inject_credentials() is True
    assert os.environ["KRX_ID"] == "myid"
    assert os.environ["KRX_PW"] == "mypw"


def test_inject_already_set(monkeypatch):
    monkeypatch.setenv("KRX_ID", "x")
    monkeypatch.setenv("KRX_PW", "y")
    with patch("subprocess.run") as run:
        assert krx_auth.inject_credentials() is True
        run.assert_not_called()


def test_inject_keychain_missing():
    with patch("subprocess.run",
               side_effect=subprocess.CalledProcessError(44, "security")):
        assert krx_auth.inject_credentials() is False
    assert "KRX_ID" not in os.environ


def test_inject_acct_parse_failure():
    with patch("subprocess.run", side_effect=_mock_security("no acct here", "pw")):
        assert krx_auth.inject_credentials() is False
