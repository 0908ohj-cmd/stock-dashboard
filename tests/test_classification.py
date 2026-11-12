import pytest

from data import classification as cls


@pytest.fixture(autouse=True)
def tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(cls, 'STORE_DIR', tmp_path)
    cls._cache.clear()


def raw(**kw):
    base = {'companyName': 'nLIGHT', 'primaryTheme': 'DEFENSE_DIRECTED_ENERGY',
            'secondaryThemes': ['OPTICAL_INTERCONNECT'], 'confidence': 'high',
            'why': '방산 매출 67%', 'evidenceUrls': ['https://www.sec.gov/x'], 'detail': '방산 레이저'}
    base.update(kw)
    return base


def test_근거와_신뢰도가_있으면_승인():
    r = cls.normalize('US', raw(), 'test')
    assert r['primaryTheme'] == 'DEFENSE_DIRECTED_ENERGY'
    assert r['secondaryThemes'] == ['OPTICAL_INTERCONNECT']
    assert r['gateReasons'] == []


@pytest.mark.parametrize('kw, reason', [
    ({'evidenceUrls': []}, '근거 URL 없음'),
    ({'confidence': 'low'}, '신뢰도 low'),
    ({'primaryTheme': 'MADE_UP'}, '테마 목록에 없는 코드'),
])
def test_근거없거나_신뢰도낮거나_목록밖이면_미분류(kw, reason):
    r = cls.normalize('US', raw(**kw), 'test')
    assert r['primaryTheme'] == cls.UNCLASSIFIED
    assert any(reason in g for g in r['gateReasons'])


def test_미분류여도_제안값은_남긴다():
    r = cls.normalize('US', raw(confidence='low'), 'test')
    assert r['suggestedTheme'] == 'DEFENSE_DIRECTED_ENERGY'


def test_사용자_확정은_자동분류가_덮어쓰지_않는다():
    records = {}
    cls.set_override('US', 'LASR', 'OPTICAL_INTERCONNECT', [], '직접 확인', records)
    applied = cls.apply_auto('US', 'LASR', cls.normalize('US', raw(), 'test'), records)
    assert applied is False
    assert records['LASR']['primaryTheme'] == 'OPTICAL_INTERCONNECT'
    assert records['LASR']['overrideByUser'] is True


def test_변경_이력을_남긴다():
    records = {}
    cls.apply_auto('US', 'X', cls.normalize('US', raw(), 'a'), records)
    cls.apply_auto('US', 'X', cls.normalize('US', raw(primaryTheme='SEMI_EQUIPMENT'), 'b'), records)
    assert records['X']['primaryTheme'] == 'SEMI_EQUIPMENT'
    assert records['X']['history'][0]['primaryTheme'] == 'DEFENSE_DIRECTED_ENERGY'


def test_저장후_다시읽기와_신규종목_탐지():
    records = {}
    cls.apply_auto('US', 'A', cls.normalize('US', raw(), 't'), records)
    cls.save_store('US', records)
    assert cls.load_store('US')['A']['primaryTheme'] == 'DEFENSE_DIRECTED_ENERGY'
    assert cls.missing_tickers('US', ['A', 'NEW']) == ['NEW']
    assert cls.primary_map('US', ['A', 'NEW']) == {'A': 'DEFENSE_DIRECTED_ENERGY', 'NEW': cls.UNCLASSIFIED}


def test_KR_시장은_kr_테마목록을_쓴다():
    r = cls.normalize('KR_KOSDAQ', raw(primaryTheme='SHIPBUILDING', secondaryThemes=[]), 't')
    assert r['primaryTheme'] == 'SHIPBUILDING'


# ── 저장소 병합 (#33 배치 push 충돌 재구성) ─────────────────────

def _auto(primary, reviewed, **kw):
    rec = cls.normalize('KR_KOSPI', {'primaryTheme': primary, 'confidence': 'high',
                                     'evidenceUrls': ['https://dart.fss.or.kr/x']}, 'llm')
    rec['reviewedAt'] = reviewed
    rec.update(kw)
    return rec


def _user(primary, reviewed):
    recs = {}
    rec = cls.set_override('KR_KOSPI', 'X', primary, [], '직접 확인', recs)
    rec['reviewedAt'] = reviewed
    return rec


def test_병합_원격_사용자확정이_로컬_자동분류보다_우선한다():
    """배치가 도는 사이 앱이 확정한 OCI홀딩스가 배치 push로 사라지면 안 된다.

    로컬(배치)의 자동 분류가 더 최근이어도 원격의 사용자 확정이 이긴다.
    """
    ours = {'010060': _auto('ENERGY_TRANSITION', '2026-11-12T17:40:00+09:00')}
    theirs = {'010060': _user('HOLDING_OTHER', '2026-11-11T22:59:00+09:00')}

    merged = cls.merge_stores(ours, theirs)

    assert merged['010060']['primaryTheme'] == 'HOLDING_OTHER'
    assert merged['010060']['overrideByUser'] is True


def test_병합_한쪽에만_있는_종목은_보존한다():
    ours = {'NEW': _auto('SHIPBUILDING', '2026-11-12T17:40:00+09:00')}
    theirs = {'010060': _user('HOLDING_OTHER', '2026-11-11T22:59:00+09:00')}

    merged = cls.merge_stores(ours, theirs)

    assert set(merged) == {'NEW', '010060'}
    assert merged['NEW']['primaryTheme'] == 'SHIPBUILDING'


def test_병합_사용자확정이_없으면_reviewedAt이_최신인_쪽을_쓴다():
    ours = {'A': _auto('SHIPBUILDING', '2026-11-12T17:40:00+09:00'),
            'B': _auto('DEFENSE', '2026-11-01T10:00:00+09:00')}
    theirs = {'A': _auto('DEFENSE', '2026-11-10T10:00:00+09:00'),
              'B': _auto('NUCLEAR', '2026-11-05T10:00:00+09:00')}

    merged = cls.merge_stores(ours, theirs)

    assert merged['A']['primaryTheme'] == 'SHIPBUILDING'   # 로컬이 최신
    assert merged['B']['primaryTheme'] == 'NUCLEAR'        # 원격이 최신


def test_병합_로컬_사용자확정도_자동분류에_지지_않는다():
    """자동 분류는 사용자 확정을 덮지 않는다(apply_auto 규칙) — 병합도 같은 규칙."""
    ours = {'A': _user('DEFENSE', '2026-11-01T10:00:00+09:00')}
    theirs = {'A': _auto('NUCLEAR', '2026-11-12T10:00:00+09:00')}

    assert cls.merge_stores(ours, theirs)['A']['primaryTheme'] == 'DEFENSE'


def test_병합은_입력을_바꾸지_않는다():
    ours = {'A': _auto('SHIPBUILDING', '2026-11-12T17:40:00+09:00')}
    theirs = {'B': _auto('DEFENSE', '2026-11-10T10:00:00+09:00')}
    before = (dict(ours), dict(theirs))

    cls.merge_stores(ours, theirs)

    assert (ours, theirs) == before
