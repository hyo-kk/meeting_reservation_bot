"""
아웃룩 캘린더 연동 모듈 (Microsoft Graph API)

사전 준비:
  1. Azure Portal(portal.azure.com) → 앱 등록 → 새 등록
  2. 리디렉션 URI: 모바일/데스크톱 → https://login.microsoftonline.com/common/oauth2/nativeclient
  3. API 권한 → Microsoft Graph → 위임된 권한 → Calendars.ReadWrite, User.Read 추가
  4. .env에 OUTLOOK_CLIENT_ID=<애플리케이션(클라이언트) ID> 추가
"""
import json
import os
import requests

GRAPH_API = "https://graph.microsoft.com/v1.0"
SCOPES = ["Calendars.ReadWrite", "User.Read"]
TOKEN_CACHE_FILE = ".outlook_token.cache"
EVENT_STORE_FILE = ".outlook_events.json"


# ─────────────────────────────────────────────
# 인증
# ─────────────────────────────────────────────

def _get_token() -> str:
    """MSAL 디바이스 코드 플로우로 액세스 토큰 획득 (캐시 활용)"""
    try:
        import msal
    except ImportError:
        raise RuntimeError("msal 패키지가 필요합니다: pip install msal")

    client_id = os.environ.get("OUTLOOK_CLIENT_ID", "").strip()
    if not client_id:
        raise RuntimeError("OUTLOOK_CLIENT_ID가 .env에 설정되지 않았습니다.\n"
                           "  Azure 앱 등록 후 .env에 OUTLOOK_CLIENT_ID=<클라이언트ID> 추가")

    tenant_id = os.environ.get("OUTLOOK_TENANT_ID", "common").strip()

    cache = msal.SerializableTokenCache()
    if os.path.exists(TOKEN_CACHE_FILE):
        with open(TOKEN_CACHE_FILE) as f:
            cache.deserialize(f.read())

    app = msal.PublicClientApplication(
        client_id,
        authority=f"https://login.microsoftonline.com/{tenant_id}",
        token_cache=cache,
    )

    # 캐시에서 조용히 토큰 갱신 시도
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])
        if result and "access_token" in result:
            _save_token_cache(cache)
            return result["access_token"]

    # 최초 인증 또는 재인증 — 디바이스 코드 플로우
    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        raise RuntimeError("디바이스 코드 플로우 시작 실패")

    print(f"\n  🔑 아웃룩 로그인이 필요합니다:")
    print(f"  {flow['message']}\n")

    result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        raise RuntimeError(f"토큰 획득 실패: {result.get('error_description', '')}")

    _save_token_cache(cache)
    return result["access_token"]


def _save_token_cache(cache):
    if cache.has_state_changed:
        with open(TOKEN_CACHE_FILE, "w") as f:
            f.write(cache.serialize())


# ─────────────────────────────────────────────
# 이벤트 ID 저장소 (bd_num 없이 날짜+시설+시간으로 매핑)
# ─────────────────────────────────────────────

def _load_store() -> dict:
    if os.path.exists(EVENT_STORE_FILE):
        with open(EVENT_STORE_FILE) as f:
            return json.load(f)
    return {}


def _save_store(store: dict):
    with open(EVENT_STORE_FILE, "w") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)


def _store_key(date: str, facility: str, start: str) -> str:
    return f"{date}|{facility}|{start}"


def save_event_id(date: str, facility: str, start: str, event_id: str):
    store = _load_store()
    store[_store_key(date, facility, start)] = event_id
    _save_store(store)


def get_event_id(date: str, facility: str, start: str) -> str | None:
    return _load_store().get(_store_key(date, facility, start))


def remove_event_id(date: str, facility: str, start: str):
    store = _load_store()
    key = _store_key(date, facility, start)
    if key in store:
        del store[key]
        _save_store(store)


# ─────────────────────────────────────────────
# 캘린더 API
# ─────────────────────────────────────────────

def create_event(
    title: str,
    date: str,
    start_time: str,
    end_time: str,
    location: str = "",
    attendees: list[str] | None = None,
) -> str | None:
    """아웃룩 캘린더에 일정 생성. 성공 시 event_id 반환."""
    try:
        token = _get_token()

        attendee_list = []
        if attendees:
            for email in attendees:
                email = email.strip()
                if "@" in email:
                    attendee_list.append({
                        "emailAddress": {"address": email, "name": email},
                        "type": "required",
                    })

        body = {
            "subject": title,
            "start": {"dateTime": f"{date}T{start_time}:00", "timeZone": "Asia/Seoul"},
            "end":   {"dateTime": f"{date}T{end_time}:00",   "timeZone": "Asia/Seoul"},
            "location": {"displayName": location},
            "attendees": attendee_list,
        }

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        resp = requests.post(f"{GRAPH_API}/me/events", headers=headers, json=body)

        if resp.status_code == 201:
            event_id = resp.json()["id"]
            print("  📅 아웃룩 캘린더 일정이 등록되었습니다.")
            if attendee_list:
                invited = [a["emailAddress"]["address"] for a in attendee_list]
                print(f"     초대 발송: {', '.join(invited)}")
            return event_id
        else:
            err = resp.json().get("error", {}).get("message", "알 수 없는 오류")
            print(f"  ⚠️  아웃룩 일정 등록 실패: {err}")
            return None

    except Exception as e:
        print(f"  ⚠️  아웃룩 연동 오류: {e}")
        return None


def cancel_event(event_id: str) -> bool:
    """아웃룩 캘린더에서 일정 취소."""
    try:
        token = _get_token()
        headers = {"Authorization": f"Bearer {token}"}
        resp = requests.delete(f"{GRAPH_API}/me/events/{event_id}", headers=headers)

        if resp.status_code == 204:
            print("  📅 아웃룩 캘린더 일정이 취소되었습니다.")
            return True
        else:
            print("  ⚠️  아웃룩 일정 취소 실패")
            return False

    except Exception as e:
        print(f"  ⚠️  아웃룩 연동 오류: {e}")
        return False
