"""PDF 변환 — Markdown 리포트를 Chrome headless로 렌더링해 PDF로 저장한다.

차트 이미지가 md 파일 기준 상대경로로 참조되므로, 임시 HTML은 md와 같은 폴더에
생성한 뒤(상대경로 보존) 변환이 끝나면 삭제한다(성공/실패 무관).
"""
import logging
import subprocess
from pathlib import Path

import markdown

logger = logging.getLogger(__name__)

CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

_CSS = """<style>
body { font-family: -apple-system, "Apple SD Gothic Neo", sans-serif; max-width: 900px; margin: 32px auto; }
img { max-width: 100%; }
table { border-collapse: collapse; width: 100%; }
td, th { border: 1px solid #ccc; padding: 4px 8px; }
</style>"""


def _strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4:]
    return text


def _render_html(md_path: Path) -> str:
    text = _strip_frontmatter(md_path.read_text(encoding="utf-8"))
    body = markdown.markdown(text, extensions=["tables", "fenced_code"])
    return f"<html><head><meta charset='utf-8'>{_CSS}</head><body>{body}</body></html>"


def md_to_pdf(md_path: Path, pdf_path: Path) -> Path:
    """md_path를 렌더링해 pdf_path에 PDF로 저장하고 pdf_path를 반환한다.

    임시 HTML은 md_path와 같은 폴더에 생성한다(차트 등 상대경로 유지) → 변환 후 삭제.
    Chrome이 없거나 변환에 실패하면 RuntimeError.
    """
    md_path = Path(md_path).resolve()
    pdf_path = Path(pdf_path).resolve()

    if not Path(CHROME_PATH).exists():
        raise RuntimeError(f"Chrome을 찾을 수 없음: {CHROME_PATH}")

    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    html_path = md_path.with_suffix(".html")  # md와 같은 폴더 → 상대경로 보존
    html_path.write_text(_render_html(md_path), encoding="utf-8")

    try:
        try:
            result = subprocess.run(
                [CHROME_PATH, "--headless", "--disable-gpu", "--no-pdf-header-footer",
                 f"--print-to-pdf={pdf_path}", html_path.as_uri()],
                capture_output=True, text=True, timeout=60,
            )
        except subprocess.SubprocessError as e:
            raise RuntimeError(f"PDF 변환 실패: {e}") from e

        if result.returncode != 0 or not pdf_path.exists():
            raise RuntimeError(
                f"PDF 변환 실패(returncode={result.returncode}): {result.stderr}")
    finally:
        html_path.unlink(missing_ok=True)

    return pdf_path
