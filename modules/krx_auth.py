"""KRX 자격증명 — macOS 키체인(서비스 krx-data)에서 읽어 환경변수로 주입.

pykrx 1.2.5+는 KRX_ID/KRX_PW 환경변수로 data.krx.co.kr에 로그인한다.
비밀번호는 키체인에만 보관 (config.yaml 평문 금지 — 선례: telegram-bot-memtrack).
등록: security add-generic-password -s krx-data -a "<KRX_ID>" -w "<KRX_PW>"
"""
import logging
import os
import re
import subprocess

logger = logging.getLogger(__name__)

SERVICE = "krx-data"


def inject_credentials(service: str = SERVICE) -> bool:
    """키체인에서 KRX ID/PW를 읽어 os.environ에 설정. 실패해도 예외 없이 False."""
    if os.environ.get("KRX_ID") and os.environ.get("KRX_PW"):
        return True
    try:
        attrs = subprocess.run(
            ["security", "find-generic-password", "-s", service],
            capture_output=True, text=True, check=True, timeout=10,
        )
        m = re.search(r'"acct"<blob>="([^"]+)"', attrs.stdout + attrs.stderr)
        if not m:
            logger.warning(f"키체인 {service}: 계정(acct) 파싱 실패")
            return False
        pw = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-w"],
            capture_output=True, text=True, check=True, timeout=10,
        )
        os.environ["KRX_ID"] = m.group(1)
        os.environ["KRX_PW"] = pw.stdout.strip()
        logger.info("KRX 자격증명 주입 완료 (키체인 krx-data)")
        return True
    except Exception as e:
        logger.warning(f"KRX 자격증명 주입 실패({type(e).__name__}) — KRX 수집은 폴백 예정")
        return False
