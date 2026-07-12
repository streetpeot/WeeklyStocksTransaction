from pathlib import Path
from unittest import mock

import pytest

from modules import pdf_export

SAMPLE = """---
title: "테스트"
type: output
---

# 테스트 리포트

본문 내용입니다.

![차트](chart.png)
"""


def _write_sample(tmp_path: Path) -> Path:
    md_path = tmp_path / "report.md"
    md_path.write_text(SAMPLE, encoding="utf-8")
    (tmp_path / "chart.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    return md_path


def test_md_to_pdf_creates_real_pdf_and_cleans_temp_html(tmp_path: Path):
    md_path = _write_sample(tmp_path)
    pdf_path = tmp_path / "out" / "report.pdf"

    result = pdf_export.md_to_pdf(md_path, pdf_path)

    assert result == pdf_path
    assert pdf_path.exists()
    assert pdf_path.read_bytes().startswith(b"%PDF")
    assert pdf_path.stat().st_size > 1000
    # 임시 HTML은 md와 같은 폴더에 만들어졌다가 삭제되어야 한다
    assert list(md_path.parent.glob("*.html")) == []


def test_md_to_pdf_raises_when_chrome_missing(tmp_path: Path):
    md_path = _write_sample(tmp_path)
    pdf_path = tmp_path / "report.pdf"

    with mock.patch.object(pdf_export, "CHROME_PATH", "/nonexistent/chrome"):
        with pytest.raises(RuntimeError):
            pdf_export.md_to_pdf(md_path, pdf_path)


def test_md_to_pdf_accepts_relative_paths(tmp_path: Path, monkeypatch):
    _write_sample(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = pdf_export.md_to_pdf(Path("report.md"), Path("out/report.pdf"))

    assert result.exists()
    assert result.read_bytes().startswith(b"%PDF")


def test_md_to_pdf_raises_on_conversion_failure_and_cleans_temp_html(tmp_path: Path):
    md_path = _write_sample(tmp_path)
    pdf_path = tmp_path / "report.pdf"

    with mock.patch.object(
        pdf_export.subprocess, "run",
        return_value=mock.Mock(returncode=1, stderr="boom"),
    ):
        with pytest.raises(RuntimeError):
            pdf_export.md_to_pdf(md_path, pdf_path)

    assert list(md_path.parent.glob("*.html")) == []
