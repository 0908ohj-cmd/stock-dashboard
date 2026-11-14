"""앱의 분류 저장 → GitHub 커밋 (#33 앱 → 배치 방향).

GitHub contents API를 흉내 내는 가짜 HTTP로 검사한다 — 실제 네트워크는 타지 않는다.
"""
import base64
import json

import pytest

from data import classification as cls
from data import classification_sync as sync

REPO = 'owner/repo'
URL = f'https://api.github.com/repos/{REPO}/contents/data/classification/kr.json'


@pytest.fixture(autouse=True)
def tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(cls, 'STORE_DIR', tmp_path)
    cls._cache.clear()


def _rec(primary, reviewed, user=False):
    return {'primaryTheme': primary, 'confidence': 'high', 'reviewedAt': reviewed,
            'overrideByUser': user, 'method': 'user' if user else 'llm', 'history': []}


def _body(records):
    return {'market': 'kr', 'taxonomyVersion': 'kr-v1', 'updatedAt': '2026-11-11T22:59:00+09:00',
            'records': records}


class _Resp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self.ok = 200 <= status < 300
        self._payload = payload or {}
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload


class FakeGitHub:
    """contents API 한 파일. PUT의 sha가 현재 sha와 다르면 409 (GitHub 동작과 같다)."""

    def __init__(self, body, put_status=None, large=False):
        self.body = body
        self.sha = 'sha0'
        self.puts = []
        self.put_status = put_status
        self.large = large             # 1MB 초과 파일은 contents API가 내용 없이 준다
        self.before_put = None         # PUT 직전에 원격을 바꾸는 훅 (동시 커밋 흉내)

    def _b64(self):
        return base64.b64encode(json.dumps(self.body, ensure_ascii=False).encode()).decode()

    def get(self, url, headers=None, timeout=None):
        if url == URL:
            if self.large:
                return _Resp(200, {'sha': self.sha, 'encoding': 'none', 'content': ''})
            return _Resp(200, {'sha': self.sha, 'encoding': 'base64', 'content': self._b64()})
        if url == f'https://api.github.com/repos/{REPO}/git/blobs/{self.sha}':
            return _Resp(200, {'sha': self.sha, 'encoding': 'base64', 'content': self._b64()})
        return _Resp(404)

    def put(self, url, json=None, headers=None, timeout=None):
        assert url == URL
        if self.before_put:
            self.before_put(self)
        self.puts.append(json)
        if self.put_status:
            return _Resp(self.put_status, {'message': 'Bad credentials'})
        if json.get('sha') != self.sha:
            return _Resp(409, {'message': f'is at {self.sha} but expected {json.get("sha")}'})
        self.body = __import__('json').loads(base64.b64decode(json['content']).decode('utf-8'))
        self.sha = f'sha{len(self.puts)}'
        return _Resp(200, {'content': {'sha': self.sha}})


def _override(ticker, primary):
    return lambda recs: cls.set_override('KR_KOSPI', ticker, primary, [], '직접 확인', recs)


def _commit(gh, apply, token='tok'):
    return sync.commit_store_change('KR_KOSPI', apply, token=token, repo=REPO,
                                    message='data: 테마 수동 확정', http=gh)


def _put_records(put):
    return json.loads(base64.b64decode(put['content']).decode('utf-8'))['records']


def test_원격의_새_변경을_보존하고_해당_종목만_바꿔_PUT한다():
    """이 서버가 마지막으로 동기화한 뒤 origin에 들어온 OCI홀딩스 확정이 덮이지 않는다.

    예전에는 서버 로컬 파일 전체를 PUT해서 원격에만 있던 확정·신규 분류가 사라졌다.
    """
    gh = FakeGitHub(_body({
        '010060': _rec('HOLDING_OTHER', '2026-11-11T22:59:00+09:00', user=True),   # 다른 사용자의 확정
        '000001': _rec('SHIPBUILDING', '2026-11-12T08:00:00+09:00'),               # 배치의 신규 분류
    }))

    res = _commit(gh, _override('005930', 'SEMI_MEMORY'))

    assert res.ok, res.message
    put = _put_records(gh.puts[-1])
    assert put['010060']['overrideByUser'] is True
    assert put['010060']['primaryTheme'] == 'HOLDING_OTHER'
    assert put['000001']['primaryTheme'] == 'SHIPBUILDING'
    assert put['005930']['primaryTheme'] == 'SEMI_MEMORY'
    assert gh.puts[-1]['sha'] == 'sha0'
    assert res.records == put          # 로컬 파일을 원격과 맞출 때 쓰는 값


def test_409면_원격을_다시_읽어_한_번_재시도한다():
    gh = FakeGitHub(_body({'000001': _rec('SHIPBUILDING', '2026-11-12T08:00:00+09:00')}))

    def concurrent_commit(g):   # 첫 PUT 직전에 다른 커밋이 들어온다
        g.before_put = None
        g.body = _body({**g.body['records'],
                        '010060': _rec('HOLDING_OTHER', '2026-11-12T09:00:00+09:00', user=True)})
        g.sha = 'sha-other'

    gh.before_put = concurrent_commit

    res = _commit(gh, _override('005930', 'SEMI_MEMORY'))

    assert res.ok, res.message
    assert len(gh.puts) == 2
    assert set(gh.body['records']) == {'000001', '010060', '005930'}


def test_409가_계속되면_실패_사유를_돌려준다():
    gh = FakeGitHub(_body({}))

    def always_moves(g):
        g.sha = f'moved{len(g.puts)}'

    gh.before_put = always_moves

    res = _commit(gh, _override('005930', 'SEMI_MEMORY'))

    assert not res.ok
    assert len(gh.puts) == 2           # 최초 1회 + 재시도 1회
    assert '409' in res.message
    assert '토큰이 없어' not in res.message


def test_토큰이_없으면_HTTP를_부르지_않고_그_사유를_돌려준다():
    class NoHttp:
        def get(self, *a, **k):
            raise AssertionError('HTTP 호출됨')
        put = get

    res = _commit(NoHttp(), _override('005930', 'SEMI_MEMORY'), token='')

    assert not res.ok
    assert '토큰' in res.message


def test_push_실패를_토큰_없음으로_표시하지_않는다():
    """토큰은 있는데 PUT이 거절되면 그 HTTP 사유를 보여준다 (예전엔 '토큰이 없어'로 안내)."""
    gh = FakeGitHub(_body({}), put_status=401)

    res = _commit(gh, _override('005930', 'SEMI_MEMORY'))

    assert not res.ok
    assert '401' in res.message
    assert '토큰이 없어' not in res.message


def test_1MB가_넘는_원격_파일은_blob_API로_내용을_읽는다():
    """contents API는 1MB 넘는 파일의 content를 비워 준다 — 빈 기록으로 PUT하면 전부 사라진다."""
    gh = FakeGitHub(_body({'000001': _rec('SHIPBUILDING', '2026-11-12T08:00:00+09:00')}), large=True)

    res = _commit(gh, _override('005930', 'SEMI_MEMORY'))

    assert res.ok, res.message
    assert set(_put_records(gh.puts[-1])) == {'000001', '005930'}


@pytest.mark.parametrize('get_resp', [
    _Resp(500),
    _Resp(404),
    _Resp(200, {'sha': 'sha0', 'encoding': 'base64',
                'content': base64.b64encode(b'not json').decode()}),
])
def test_원격_내용을_읽지_못하면_PUT하지_않는다(get_resp):
    class Broken(FakeGitHub):
        def get(self, url, headers=None, timeout=None):
            return get_resp

    gh = Broken(_body({}))

    res = _commit(gh, _override('005930', 'SEMI_MEMORY'))

    assert not res.ok
    assert gh.puts == []


def test_네트워크_예외는_사유와_함께_실패로_돌려준다():
    class Down(FakeGitHub):
        def get(self, url, headers=None, timeout=None):
            raise TimeoutError('read timed out')

    res = _commit(Down(_body({})), _override('005930', 'SEMI_MEMORY'))

    assert not res.ok
    assert 'TimeoutError' in res.message
