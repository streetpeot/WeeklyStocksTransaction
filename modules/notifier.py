"""텔레그램 발신 — 토큰은 macOS 키체인에서만 읽는다 (config 평문 금지)."""
import subprocess
import time
from pathlib import Path

import requests

KEYCHAIN_SERVICE = "telegram-bot-memtrack"
KEYCHAIN_ACCOUNT = "sjbossa"


def get_bot_token() -> str:
    r = subprocess.run(
        ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE,
         "-a", KEYCHAIN_ACCOUNT, "-w"],
        capture_output=True, text=True)
    token = r.stdout.strip()
    if r.returncode != 0 or not token:
        raise RuntimeError(
            f"키체인에서 봇 토큰을 읽지 못함 (service={KEYCHAIN_SERVICE}). "
            "등록: security add-generic-password -U -s telegram-bot-memtrack -a sjbossa -w <token>")
    return token


def _post(method: str, *, data: dict, files: dict | None = None) -> None:
    url = f"https://api.telegram.org/bot{get_bot_token()}/{method}"
    last = None
    for attempt in range(2):  # 1회 재시도
        try:
            resp = requests.post(url, data=data, files=files, timeout=60)
            if resp.status_code == 200 and resp.json().get("ok"):
                return
            last = f"HTTP {resp.status_code}: {resp.json()}"
        except requests.RequestException as e:
            last = str(e)
        if attempt == 0:
            time.sleep(3)
    raise RuntimeError(f"텔레그램 {method} 실패: {last}")


def send_document(chat_id: str, file_path: Path, caption: str) -> None:
    payload = Path(file_path).read_bytes()
    _post("sendDocument", data={"chat_id": chat_id, "caption": caption},
          files={"document": (Path(file_path).name, payload)})


def send_message(chat_id: str, text: str) -> None:
    _post("sendMessage", data={"chat_id": chat_id, "text": text})
