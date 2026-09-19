"""시크릿 해석 — KIS·AI 키는 macOS 키체인에서만 읽는다 (config 평문 금지).

선례: modules/notifier.py(텔레그램 토큰).
KIS는 app_key·app_secret이 쌍이므로 항목 하나에 담는다
(acct=app_key, 비밀번호=app_secret).

등록 (사람이 한다):
    security add-generic-password -U -s wst-kis -a "<APP_KEY>" -w "<APP_SECRET>"
    security add-generic-password -U -s wst-anthropic -a sjbossa -w "<API_KEY>"
"""
import copy
import re
import subprocess

KIS_SERVICE = "wst-kis"

# reporter._call_ai가 분기하는 provider와 1:1. 선언된 provider의 키만 해석한다
# — 고정 목록으로 걸면 provider를 바꾸는 순간 부팅이 막힌다.
AI_SERVICES = {
    "anthropic": "wst-anthropic",
    "openai": "wst-openai",
    "google": "wst-google",
}
AI_ACCOUNT = "sjbossa"


def _find(service: str, *, with_password: bool) -> subprocess.CompletedProcess:
    cmd = ["security", "find-generic-password", "-s", service]
    if with_password:
        cmd.append("-w")
    return subprocess.run(cmd, capture_output=True, text=True, timeout=10)


def _hint(service: str, account: str) -> str:
    return (f"키체인에서 읽지 못함 (service={service}). "
            f'등록: security add-generic-password -U -s {service} -a "{account}" -w <값>')


def get_kis_credentials(service: str = KIS_SERVICE) -> tuple[str, str]:
    """키체인 항목 하나에서 (app_key, app_secret)을 읽는다."""
    attrs = _find(service, with_password=False)
    m = re.search(r'"acct"<blob>="([^"]+)"', attrs.stdout + attrs.stderr)
    pw = _find(service, with_password=True)
    if attrs.returncode != 0 or m is None or pw.returncode != 0 or not pw.stdout.strip():
        raise RuntimeError(_hint(service, "<APP_KEY>"))
    return m.group(1), pw.stdout.strip()


def get_ai_api_key(provider: str) -> str:
    """선언된 provider에 해당하는 키체인 항목에서 API 키를 읽는다."""
    service = AI_SERVICES.get(provider.lower())
    if service is None:
        raise RuntimeError(
            f"알 수 없는 AI provider: {provider} (지원: {', '.join(sorted(AI_SERVICES))})")
    r = _find(service, with_password=True)
    key = r.stdout.strip()
    if r.returncode != 0 or not key:
        raise RuntimeError(_hint(service, AI_ACCOUNT))
    return key


def resolve(config: dict) -> dict:
    """config에 키체인 시크릿을 주입한 새 dict를 돌려준다 (원본 불변)."""
    out = copy.deepcopy(config)
    app_key, app_secret = get_kis_credentials()
    out.setdefault("kis", {})["app_key"] = app_key
    out["kis"]["app_secret"] = app_secret

    ai = out.setdefault("ai", {})
    ai["api_key"] = get_ai_api_key(ai.get("provider", "anthropic"))
    return out
