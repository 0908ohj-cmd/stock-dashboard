import base64
import json
import pathlib
import socket
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'scripts'))

import sync_leaderboard as sync


@pytest.fixture(autouse=True)
def 실제_네트워크_차단(monkeypatch):
    """이 모듈의 테스트가 실제 HTTP를 내지 못하게 막는다.

    목을 빠뜨린 테스트가 조용히 진짜 GitHub API를 때리면 원격 파일이 바뀐다 —
    실패로 드러나게 한다. requests와 소켓 양쪽을 막아 우회 경로를 남기지 않는다.
    """
    def _차단(*args, **kwargs):
        raise AssertionError('테스트에서 실제 네트워크 호출이 발생했습니다 (목 누락)')

    for name in ('get', 'put', 'post', 'patch', 'delete', 'request'):
        monkeypatch.setattr(sync.requests, name, _차단, raising=False)
    monkeypatch.setattr(socket.socket, 'connect', _차단)
    monkeypatch.setattr(socket.socket, 'connect_ex', _차단)


class _FakeResponse:
    """requests.Response 흉내 — push_to_github가 쓰는 속성만 갖는다."""

    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._payload = payload if payload is not None else {}
        self.text = ''

    def json(self):
        return self._payload


def _원격응답(envelope, sha='sha-old'):
    """Contents API의 GET 응답(내용 + sha)을 흉내낸다."""
    content = base64.b64encode(
        json.dumps(envelope, ensure_ascii=False, indent=2).encode()).decode()
    return _FakeResponse(200, {'sha': sha, 'content': content, 'encoding': 'base64'})


US_SOURCE = {
    'updated_at': '2026-07-29T07:02:16',
    'items': [
        {
            'ticker': 'DELL', 'sources': ['leaders', 'leader_ride'],
            'added_at': '2026-07-03', 'last_seen': '2026-07-29',
            'close': 426.91, 'perf_1m': 4.4, 'perf_3m': 97.9,
            'perf_6m': 271.6, 'perf_12m': 236.9, 'rs_rating': 96,
            'weighted_perf': 0.5563, 'dist_from_52w_high': -8.2,
            'dist_from_52w_low': 286.2, 'dollar_vol_w': 18879041930,
            'adr': 7.4, 'base_len_argmax': 38, 'theme': 'AI 서버 인프라',
            'sector': '컴퓨터·서버',
        },
    ],
}

KR_SOURCE = {
    'updated_at': '2026-07-29T16:36:40',
    'items': [
        {
            'rank': 1, 'ticker': '005930', 'name': '삼성전자',
            'market': 'KOSPI', 'sources': ['leaders'], 'sector': '반도체',
            'close': 71000, 'market_cap': 420000000000000,
            'avg_dollar_vol': 850000000000, 'adr': 2.4,
            'dist_from_52w_high': -5.1, 'dist_from_52w_low': 42.0,
            'perf_1m': 3.2, 'perf_3m': 18.0, 'perf_6m': 30.5,
            'perf_12m': 41.2, 'rs_rating': 88, 'theme': '반도체',
        },
    ],
}


def test_normalize_us_주간거래대금을_일평균으로_환산():
    items = sync.normalize_us(US_SOURCE)
    assert items[0]['avg_dollar_vol'] == round(18879041930 / 5)


def test_normalize_us_거래대금_0은_0으로_남는다():
    """0은 '값 없음'이 아니라 '거래가 없었다'는 실제 값이다 — null로 바꾸면 안 된다."""
    payload = {'updated_at': 'x', 'items': [dict(US_SOURCE['items'][0], dollar_vol_w=0)]}
    assert sync.normalize_us(payload)[0]['avg_dollar_vol'] == 0


def test_normalize_us_거래대금이_없으면_null():
    payload = {'updated_at': 'x', 'items': [
        {k: v for k, v in US_SOURCE['items'][0].items() if k != 'dollar_vol_w'}]}
    assert sync.normalize_us(payload)[0]['avg_dollar_vol'] is None


def test_normalize_us_이름은_빈문자열_섹터는_소스값():
    """US 소스엔 종목명이 없지만 섹터는 있다 — 섹터는 그대로 실어 보낸다."""
    items = sync.normalize_us(US_SOURCE)
    assert items[0]['name'] == ''
    assert items[0]['sector'] == '컴퓨터·서버'
    assert items[0]['market'] == 'US'


def test_normalize_us_섹터가_없는_구소스는_null():
    """섹터 도입(2026-08-17) 전 스냅샷이나 유예 승계 항목엔 sector 키가 아예 없다."""
    payload = {'updated_at': 'x', 'items': [
        {k: v for k, v in US_SOURCE['items'][0].items() if k != 'sector'}]}
    assert sync.normalize_us(payload)[0]['sector'] is None


def test_normalize_us_필드_보존():
    items = sync.normalize_us(US_SOURCE)
    it = items[0]
    assert it['ticker'] == 'DELL'
    assert it['rs_rating'] == 96
    assert it['adr'] == 7.4
    assert it['added_at'] == '2026-07-03'
    assert it['theme'] == 'AI 서버 인프라'
    assert it['sources'] == ['leaders', 'leader_ride']


def test_normalize_kr_이름_섹터_유지_added_at은_null():
    items = sync.normalize_kr(KR_SOURCE)
    it = items[0]
    assert it['name'] == '삼성전자'
    assert it['sector'] == '반도체'
    assert it['market'] == 'KOSPI'
    assert it['added_at'] is None


def test_normalize_kr_거래대금은_그대로():
    items = sync.normalize_kr(KR_SOURCE)
    assert items[0]['avg_dollar_vol'] == 850000000000


def test_양_시장_키_구성이_동일():
    us_keys = set(sync.normalize_us(US_SOURCE)[0].keys())
    kr_keys = set(sync.normalize_kr(KR_SOURCE)[0].keys())
    assert us_keys == kr_keys == set(sync.COMMON_KEYS)


def test_sort_and_rank_leaders_우선_그다음_RS내림차순():
    items = [
        {'ticker': 'A', 'sources': ['leader_ride'], 'rs_rating': 99, 'avg_dollar_vol': 100},
        {'ticker': 'B', 'sources': ['leaders'], 'rs_rating': 85, 'avg_dollar_vol': 100},
        {'ticker': 'C', 'sources': ['leaders'], 'rs_rating': 92, 'avg_dollar_vol': 100},
    ]
    ranked = sync.sort_and_rank(items)
    assert [it['ticker'] for it in ranked] == ['C', 'B', 'A']
    assert [it['rank'] for it in ranked] == [1, 2, 3]


def test_sort_and_rank_RS동률이면_거래대금_큰순():
    items = [
        {'ticker': 'A', 'sources': ['leaders'], 'rs_rating': 90, 'avg_dollar_vol': 100},
        {'ticker': 'B', 'sources': ['leaders'], 'rs_rating': 90, 'avg_dollar_vol': 500},
    ]
    ranked = sync.sort_and_rank(items)
    assert [it['ticker'] for it in ranked] == ['B', 'A']


def test_sort_and_rank_null값은_뒤로():
    items = [
        {'ticker': 'A', 'sources': ['leaders'], 'rs_rating': None, 'avg_dollar_vol': None},
        {'ticker': 'B', 'sources': ['leaders'], 'rs_rating': 70, 'avg_dollar_vol': 10},
    ]
    ranked = sync.sort_and_rank(items)
    assert [it['ticker'] for it in ranked] == ['B', 'A']


def test_kr_소스의_기존_rank는_무시하고_재부여():
    payload = {'updated_at': 'x', 'items': [
        dict(KR_SOURCE['items'][0], rank=9, ticker='005930', rs_rating=80),
        dict(KR_SOURCE['items'][0], rank=1, ticker='000660', rs_rating=95),
    ]}
    ranked = sync.sort_and_rank(sync.normalize_kr(payload))
    assert ranked[0]['ticker'] == '000660'
    assert ranked[0]['rank'] == 1


def test_build_envelope():
    items = sync.sort_and_rank(sync.normalize_us(US_SOURCE))
    env = sync.build_envelope('us', items, US_SOURCE['updated_at'])
    assert env['market'] == 'US'
    assert env['count'] == 1
    assert env['source_updated_at'] == '2026-07-29T07:02:16'
    assert env['synced_at']
    assert env['items'][0]['ticker'] == 'DELL'


def test_build_envelope_빈_리스트도_정상():
    env = sync.build_envelope('kr', [], '2026-07-29T16:36:40')
    assert env['count'] == 0
    assert env['items'] == []


def test_load_source_us(tmp_path):
    (tmp_path / 'leaderboard.json').write_text(
        json.dumps(US_SOURCE, ensure_ascii=False), encoding='utf-8')
    payload = sync.load_source('us', tmp_path)
    assert payload['updated_at'] == '2026-07-29T07:02:16'


def test_load_source_kr_파일명(tmp_path):
    (tmp_path / 'leaderboard_kr.json').write_text(
        json.dumps(KR_SOURCE, ensure_ascii=False), encoding='utf-8')
    payload = sync.load_source('kr', tmp_path)
    assert payload['items'][0]['ticker'] == '005930'


def test_load_source_파일_없으면_예외(tmp_path):
    with pytest.raises(FileNotFoundError):
        sync.load_source('us', tmp_path)


def test_sync_market_기본은_로컬파일만_생성(tmp_path, monkeypatch):
    src = tmp_path / 'src'
    src.mkdir()
    (src / 'leaderboard.json').write_text(
        json.dumps(US_SOURCE, ensure_ascii=False), encoding='utf-8')
    out = tmp_path / 'out'
    out.mkdir()
    monkeypatch.setattr(sync, 'OUTPUT_DIR', out)

    env = sync.sync_market('us', src, push=False, token=None)

    written = json.loads((out / 'us.json').read_text(encoding='utf-8'))
    assert written['count'] == 1
    assert written['items'][0]['rank'] == 1
    assert env['count'] == 1


def test_sync_market_빈_items도_정상_기록(tmp_path, monkeypatch):
    """0개는 주도주 부재 신호 — 장애가 아니므로 그대로 쓴다."""
    src = tmp_path / 'src'
    src.mkdir()
    (src / 'leaderboard_kr.json').write_text(
        json.dumps({'updated_at': '2026-07-29T16:36:40', 'items': []}),
        encoding='utf-8')
    out = tmp_path / 'out'
    out.mkdir()
    monkeypatch.setattr(sync, 'OUTPUT_DIR', out)

    sync.sync_market('kr', src, push=False, token=None)

    written = json.loads((out / 'kr.json').read_text(encoding='utf-8'))
    assert written['count'] == 0
    assert written['items'] == []


def test_main_push_없으면_원격을_건드리지_않는다(tmp_path, monkeypatch):
    """인자 없이 실행하면 로컬 파일만 갱신 — 토큰 조회조차 하지 않는다."""
    src = tmp_path / 'src'
    src.mkdir()
    (src / 'leaderboard.json').write_text(
        json.dumps(US_SOURCE, ensure_ascii=False), encoding='utf-8')
    out = tmp_path / 'out'
    out.mkdir()
    monkeypatch.setattr(sync, 'OUTPUT_DIR', out)
    pushed = []
    monkeypatch.setattr(sync, 'push_to_github',
                        lambda market, env, token: pushed.append(market))

    def _토큰조회_금지():
        raise AssertionError('푸시하지 않는데 토큰을 조회했습니다')
    monkeypatch.setattr(sync, 'get_token', _토큰조회_금지)

    rc = sync.main(['--market', 'us', '--source-dir', str(src)])

    assert rc == 0
    assert pushed == []
    assert (out / 'us.json').exists()


def test_main_push_주면_푸시하고_로컬은_안_건드린다(tmp_path, monkeypatch):
    src = tmp_path / 'src'
    src.mkdir()
    (src / 'leaderboard.json').write_text(
        json.dumps(US_SOURCE, ensure_ascii=False), encoding='utf-8')
    out = tmp_path / 'out'
    out.mkdir()
    monkeypatch.setattr(sync, 'OUTPUT_DIR', out)
    pushed = []
    monkeypatch.setattr(sync, 'push_to_github',
                        lambda market, env, token: pushed.append(market))
    monkeypatch.setattr(sync, 'get_token', lambda: 'fake-token')

    rc = sync.main(['--market', 'us', '--source-dir', str(src), '--push'])

    assert rc == 0
    assert pushed == ['us']
    assert not (out / 'us.json').exists()     # 로컬 작업 트리는 그대로


def test_main_소스_없으면_exit1_푸시_안함(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(sync, 'push_to_github', lambda *a, **k: calls.append(a))
    monkeypatch.setattr(sync, 'get_token', lambda: 'fake-token')
    rc = sync.main(['--market', 'us', '--source-dir', str(tmp_path), '--push'])
    assert rc == 1
    assert calls == []


def test_main_all_한쪽_실패해도_다른쪽은_푸시(tmp_path, monkeypatch):
    src = tmp_path / 'src'
    src.mkdir()
    (src / 'leaderboard.json').write_text(
        json.dumps(US_SOURCE, ensure_ascii=False), encoding='utf-8')
    # KR 소스는 일부러 만들지 않는다
    pushed = []
    monkeypatch.setattr(sync, 'push_to_github',
                        lambda market, env, token: pushed.append(market))
    monkeypatch.setattr(sync, 'get_token', lambda: 'fake-token')

    rc = sync.main(['--market', 'all', '--source-dir', str(src), '--push'])

    assert rc == 1              # KR 실패
    assert pushed == ['us']     # US는 정상 푸시


def test_get_token_환경변수_우선(monkeypatch):
    monkeypatch.setenv('DASHBOARD_GITHUB_TOKEN', 'env-token')
    assert sync.get_token() == 'env-token'


def test_main_토큰_조회_실패도_시장별_로그와_exit1(tmp_path, monkeypatch, capsys):
    """토큰 실패가 트레이스백으로 새면 안 된다 — 2026-07-30 배치가 그렇게 죽었다.

    구현이 get_token()을 try 밖에서 부르면 main이 예외로 빠져나가
    '[market] 실패' 로그도 종료 코드도 남지 않는다.
    """
    src = tmp_path / 'src'
    src.mkdir()
    (src / 'leaderboard.json').write_text(
        json.dumps(US_SOURCE, ensure_ascii=False), encoding='utf-8')
    (src / 'leaderboard_kr.json').write_text(
        json.dumps(KR_SOURCE, ensure_ascii=False), encoding='utf-8')
    pushed = []
    monkeypatch.setattr(sync, 'push_to_github',
                        lambda market, env, token: pushed.append(market))

    def _토큰없음():
        raise RuntimeError('GitHub 토큰을 찾을 수 없습니다.')
    monkeypatch.setattr(sync, 'get_token', _토큰없음)

    rc = sync.main(['--market', 'all', '--source-dir', str(src), '--push'])

    assert rc == 1
    assert pushed == []
    err = capsys.readouterr().err
    assert '[us] 실패' in err and '[kr] 실패' in err
    assert '토큰' in err


def test_네트워크_차단_가드가_실제로_동작(monkeypatch):
    """가드 자체의 회귀 방지 — 목 없이 호출하면 반드시 실패해야 한다."""
    with pytest.raises(AssertionError):
        sync.requests.get('https://api.github.com/x')
    with pytest.raises(AssertionError):
        socket.socket().connect(('127.0.0.1', 9))


# ── push_to_github: GET(sha·내용) → 변경 시에만 PUT ────────────────────────────

def _봉투(count=1, updated='2026-07-29T07:02:16'):
    items = sync.sort_and_rank(sync.normalize_us(US_SOURCE))[:count]
    return sync.build_envelope('us', items, updated)


def test_push_내용이_같으면_PUT_생략(monkeypatch, capsys):
    """synced_at만 다른 동일 내용은 푸시하지 않는다 — 빈 커밋·재배포 방지."""
    envelope = _봉투()
    remote = dict(envelope, synced_at='2020-01-01T00:00:00')   # 시각만 다르다
    puts = []
    monkeypatch.setattr(sync.requests, 'get', lambda *a, **k: _원격응답(remote))
    monkeypatch.setattr(sync.requests, 'put',
                        lambda *a, **k: puts.append(k) or _FakeResponse(200))

    assert sync.push_to_github('us', envelope, 'tok') is False
    assert puts == []
    assert '건너뜀' in capsys.readouterr().out


def test_push_내용이_다르면_sha와_함께_PUT(monkeypatch):
    """L10: GET으로 sha를 얻어 PUT 본문에 실어 보내는 계약."""
    envelope = _봉투()
    remote = dict(envelope, count=0, items=[])                 # 내용이 실제로 다르다
    puts = []
    monkeypatch.setattr(sync.requests, 'get', lambda *a, **k: _원격응답(remote))
    monkeypatch.setattr(sync.requests, 'put',
                        lambda *a, **k: puts.append(k) or _FakeResponse(200))

    assert sync.push_to_github('us', envelope, 'tok') is True
    assert len(puts) == 1
    body = puts[0]['json']
    assert body['sha'] == 'sha-old'
    보낸내용 = json.loads(base64.b64decode(body['content']).decode('utf-8'))
    assert 보낸내용['count'] == 1


def test_push_원격_파일이_없으면_sha_없이_생성(monkeypatch):
    puts = []
    monkeypatch.setattr(sync.requests, 'get', lambda *a, **k: _FakeResponse(404))
    monkeypatch.setattr(sync.requests, 'put',
                        lambda *a, **k: puts.append(k) or _FakeResponse(201))

    assert sync.push_to_github('us', _봉투(), 'tok') is True
    assert 'sha' not in puts[0]['json']


def test_push_같은날_시각만_바뀌면_PUT_생략(monkeypatch):
    """같은 날 재실행·재시도는 커밋을 만들지 않는다 — 시각만 다르고 내용은 같다."""
    envelope = _봉투(updated='2026-07-29T07:31:00')
    remote = dict(envelope, source_updated_at='2026-07-29T07:02:16',
                  synced_at='2026-07-29T07:05:00')
    puts = []
    monkeypatch.setattr(sync.requests, 'get', lambda *a, **k: _원격응답(remote))
    monkeypatch.setattr(sync.requests, 'put',
                        lambda *a, **k: puts.append(k) or _FakeResponse(200))

    assert sync.push_to_github('us', envelope, 'tok') is False
    assert puts == []


def test_push_날짜가_바뀌면_종목이_같아도_PUT(monkeypatch):
    """날이 바뀌면 목록이 똑같아도 푸시한다 — 신선도 시각을 전진시켜야 한다."""
    envelope = _봉투(updated='2026-07-30T07:01:00')
    remote = dict(envelope, source_updated_at='2026-07-29T07:02:16',
                  synced_at='2026-07-29T07:05:00')
    puts = []
    monkeypatch.setattr(sync.requests, 'get', lambda *a, **k: _원격응답(remote))
    monkeypatch.setattr(sync.requests, 'put',
                        lambda *a, **k: puts.append(k) or _FakeResponse(200))

    assert sync.push_to_github('us', envelope, 'tok') is True
    보낸내용 = json.loads(base64.b64decode(puts[0]['json']['content']).decode('utf-8'))
    assert 보낸내용['source_updated_at'] == '2026-07-30T07:01:00'


def test_push_0종목이_연일_이어져도_갱신시각은_전진한다(monkeypatch):
    """실제 사례: KR은 매일 0종목이라 날짜 말고는 다른 게 없다.

    여기서 PUT을 생략하면 미러의 source_updated_at이 2026-07-29에 얼어붙고,
    정상 동작하는 파이프라인이 KR 탭에 영원히 ⚠️ 갱신 지연으로 표시된다.
    """
    envelope = sync.build_envelope('kr', [], '2026-07-30T16:33:10')
    remote = dict(envelope, source_updated_at='2026-07-29T16:36:40',
                  synced_at='2026-07-29T16:40:00')
    puts = []
    monkeypatch.setattr(sync.requests, 'get', lambda *a, **k: _원격응답(remote))
    monkeypatch.setattr(sync.requests, 'put',
                        lambda *a, **k: puts.append(k) or _FakeResponse(200))

    assert sync.push_to_github('kr', envelope, 'tok') is True
    보낸내용 = json.loads(base64.b64decode(puts[0]['json']['content']).decode('utf-8'))
    assert 보낸내용['count'] == 0
    assert 보낸내용['source_updated_at'] == '2026-07-30T16:33:10'   # 시각이 전진


def test_push_같은날_0종목_재실행은_PUT_생략(monkeypatch):
    """하루 안의 재시도까지 커밋을 만들면 안 된다 — 절충의 반대편을 고정한다."""
    envelope = sync.build_envelope('kr', [], '2026-07-30T16:39:02')
    remote = dict(envelope, source_updated_at='2026-07-30T16:33:10',
                  synced_at='2026-07-30T16:35:00')
    puts = []
    monkeypatch.setattr(sync.requests, 'get', lambda *a, **k: _원격응답(remote))
    monkeypatch.setattr(sync.requests, 'put',
                        lambda *a, **k: puts.append(k) or _FakeResponse(200))

    assert sync.push_to_github('kr', envelope, 'tok') is False
    assert puts == []


@pytest.mark.parametrize('이상한_시각', [None, '', '2026', 12345, {'a': 1}])
def test_push_갱신시각이_깨져도_비교가_터지지_않는다(monkeypatch, 이상한_시각):
    """source_updated_at이 null이거나 짧거나 문자열이 아니어도 예외 없이 판단한다."""
    envelope = dict(_봉투(), source_updated_at=이상한_시각)
    remote = dict(envelope, source_updated_at='2026-07-29T07:02:16')
    puts = []
    monkeypatch.setattr(sync.requests, 'get', lambda *a, **k: _원격응답(remote))
    monkeypatch.setattr(sync.requests, 'put',
                        lambda *a, **k: puts.append(k) or _FakeResponse(200))

    assert sync.push_to_github('us', envelope, 'tok') is True   # 날짜가 다르니 푸시


def test_push_종목이_하나라도_바뀌면_PUT(monkeypatch):
    """시각이 동일해도 items가 다르면 반드시 푸시한다 — 비교가 과하게 무뎌지지 않게."""
    envelope = _봉투()
    바뀐_아이템 = [dict(envelope['items'][0], ticker='NVDA')]
    remote = dict(envelope, items=바뀐_아이템)                  # 시각은 그대로
    puts = []
    monkeypatch.setattr(sync.requests, 'get', lambda *a, **k: _원격응답(remote))
    monkeypatch.setattr(sync.requests, 'put',
                        lambda *a, **k: puts.append(k) or _FakeResponse(200))

    assert sync.push_to_github('us', envelope, 'tok') is True
    assert len(puts) == 1


def test_push_실패하면_예외(monkeypatch):
    monkeypatch.setattr(sync.requests, 'get', lambda *a, **k: _FakeResponse(404))
    monkeypatch.setattr(sync.requests, 'put',
                        lambda *a, **k: _FakeResponse(409))
    with pytest.raises(RuntimeError):
        sync.push_to_github('us', _봉투(), 'tok')
