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
