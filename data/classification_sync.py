"""분류 저장소를 GitHub(origin)에 커밋한다 — 주도섹터 탭 > 분류 검토 전용. Streamlit 무의존.

예전에는 방금 GET한 sha로 이 서버의 로컬 파일 전체를 PUT했다(#33). sha가 방금 받은 것이라
409 충돌 검사가 아무것도 막지 못했고, 이 서버가 마지막으로 동기화한 뒤 origin에 들어온 변경
(다른 사용자의 확정, 배치의 신규 분류)이 조용히 덮였다.

이제는 GET한 원격 내용 위에 바꿀 종목만 적용(apply)해서 PUT한다. 그 사이 원격이 또 바뀌어
409가 오면 원격을 다시 읽어 한 번 더 시도한다. 실패하면 사유를 그대로 돌려준다.

HTTP는 requests 호환 객체(get/put)를 주입받는다 — 테스트는 가짜를 넣는다.
"""
import base64
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from data import classification as cls

API = 'https://api.github.com'
_CONFLICT_RETRIES = 1
_LOCAL_ONLY = '이 서버에만 저장됨(다음 동기화 때 사라질 수 있음)'


@dataclass(frozen=True)
class CommitResult:
    ok: bool
    message: str                  # 화면 안내 문구 — 실패면 사유 포함
    records: dict | None = None   # 성공 시 원격에 올린 기록 (로컬 파일을 원격과 맞출 때 쓴다)


class _Fail(Exception):
    pass


def _decode(meta: dict) -> dict:
    try:
        body = json.loads(base64.b64decode(meta.get('content') or '').decode('utf-8'))
    except (ValueError, TypeError):
        raise _Fail('원격 분류 파일 형식 오류') from None
    if not isinstance(body, dict) or not isinstance(body.get('records'), dict):
        raise _Fail('원격 분류 파일 형식 오류')
    return body


def _read_remote(http, repo: str, url: str, hdrs: dict) -> tuple[dict, str]:
    r = http.get(url, headers=hdrs, timeout=10)
    if not r.ok:
        raise _Fail(f'원격 분류 파일 조회 실패(HTTP {r.status_code})')
    meta = r.json()
    sha = meta.get('sha')
    if meta.get('encoding') != 'base64' or not meta.get('content'):
        # contents API는 1MB 넘는 파일의 content를 비워 준다 — 빈 기록으로 PUT하면 전부 사라진다
        b = http.get(f'{API}/repos/{repo}/git/blobs/{sha}', headers=hdrs, timeout=10)
        if not b.ok:
            raise _Fail(f'원격 분류 파일 조회 실패(blob HTTP {b.status_code})')
        meta = b.json()
    return _decode(meta), sha


def _put_failure(status: int) -> str:
    hint = ' — 토큰 인증·권한 확인 필요' if status in (401, 403) else ''
    return f'GitHub 저장 실패(HTTP {status}{hint}) — {_LOCAL_ONLY}'


def commit_store_change(market: str, apply: Callable[[dict], object], *, token: str, repo: str,
                        message: str, http) -> CommitResult:
    """원격 분류 파일 위에 apply(records)를 적용해 커밋한다.

    apply는 records dict를 제자리에서 바꾸는 함수(예: set_override). 원격 기록을 받아 그 위에
    적용하므로, 원격에만 있는 종목·확정은 그대로 남는다.
    """
    if not token:
        return CommitResult(False, f'GitHub 토큰이 없어 {_LOCAL_ONLY}')
    rel = f'data/classification/{cls.market_key(market)}.json'
    url = f'{API}/repos/{repo}/contents/{rel}'
    hdrs = {'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json'}

    for _ in range(1 + _CONFLICT_RETRIES):
        try:
            body, sha = _read_remote(http, repo, url, hdrs)
            records = dict(body['records'])
            apply(records)
            new_body = {**body, 'updatedAt': datetime.now(cls.KST).isoformat(timespec='seconds'),
                        'records': dict(sorted(records.items()))}
            text = json.dumps(new_body, ensure_ascii=False, indent=1)   # save_store와 같은 모양
            payload = {'message': message, 'sha': sha,
                       'content': base64.b64encode(text.encode('utf-8')).decode()}
            r = http.put(url, json=payload, headers=hdrs, timeout=15)
        except _Fail as e:
            return CommitResult(False, f'{e} — {_LOCAL_ONLY}')
        except Exception as e:   # 네트워크·타임아웃 등 — 앱은 계속 떠 있어야 한다
            return CommitResult(False, f'GitHub 연결 실패({type(e).__name__}: {e}) — {_LOCAL_ONLY}')
        if r.ok:
            return CommitResult(True, 'GitHub에 커밋됨', new_body['records'])
        if r.status_code != 409:
            return CommitResult(False, _put_failure(r.status_code))
    return CommitResult(False, f'GitHub 저장 실패(HTTP 409 — 그 사이 원격이 계속 바뀌어 재시도 후에도 충돌) '
                               f'— {_LOCAL_ONLY}')
