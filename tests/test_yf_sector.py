import json

import data.yf_sector as yf_sector


def _setup(tmp_path, monkeypatch, cache_content: dict):
    cache_file = tmp_path / 'yf_sector_cache.json'
    cache_file.write_text(json.dumps(cache_content, ensure_ascii=False), encoding='utf-8')
    monkeypatch.setattr(yf_sector, '_CACHE_FILE', cache_file)
    monkeypatch.setattr(yf_sector, '_SESSION_ATTEMPTED', set())
    return cache_file


def test_map_label_industry_우선():
    assert yf_sector.map_label('Industrials', 'Aerospace & Defense') == '방위산업'
    assert yf_sector.map_label('Consumer Cyclical', 'Auto Manufacturers') == '완성차'
    assert yf_sector.map_label('Financial Services', 'Insurance - Property & Casualty') == '손해보험'


def test_map_label_대시_표기_흔들림_흡수():
    """yfinance는 버전에 따라 em대시/하이픈을 섞어 쓴다."""
    assert yf_sector.map_label('Technology', 'Software—Application') == '응용 소프트웨어'
    assert yf_sector.map_label('Technology', 'Software - Application') == '응용 소프트웨어'


def test_map_label_industry_미매핑이면_sector_폴백():
    assert yf_sector.map_label('Technology', 'Totally Unknown Industry 2099') == 'IT·기술'


def test_map_label_둘다_미매핑이면_industry_원문():
    assert yf_sector.map_label('Unknown Sector', 'Odd Industry') == 'Odd Industry'
    assert yf_sector.map_label('', '') == ''


def test_symbols_kr_시장별_접미사_순서():
    assert yf_sector._symbols('005930', 'KR_KOSPI')[0] == '005930.KS'
    assert yf_sector._symbols('005930', 'KR_KOSDAQ')[0] == '005930.KQ'
    assert yf_sector._symbols('NVDA', 'US') == ['NVDA']


def test_캐시_히트시_네트워크_미접촉(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, {'012450': '방위산업'})
    monkeypatch.setattr(yf_sector, 'fetch_label',
                        lambda *a: (_ for _ in ()).throw(AssertionError('fetch 호출됨')))
    assert yf_sector.get_yf_sectors(['012450'], 'KR_KOSPI') == {'012450': '방위산업'}


def test_미캐시분만_조회하고_저장한다(tmp_path, monkeypatch):
    cache_file = _setup(tmp_path, monkeypatch, {'012450': '방위산업'})
    calls = []

    def fake_fetch(ticker, market):
        calls.append(ticker)
        return '완성차'

    monkeypatch.setattr(yf_sector, 'fetch_label', fake_fetch)
    result = yf_sector.get_yf_sectors(['012450', '000270'], 'KR_KOSPI')

    assert calls == ['000270']
    assert result == {'012450': '방위산업', '000270': '완성차'}
    assert json.loads(cache_file.read_text(encoding='utf-8'))['000270'] == '완성차'


def test_조회_실패는_대시표시_캐시미저장_세션가드(tmp_path, monkeypatch):
    cache_file = _setup(tmp_path, monkeypatch, {})
    calls = []

    def fake_fetch(ticker, market):
        calls.append(ticker)
        return ''

    monkeypatch.setattr(yf_sector, 'fetch_label', fake_fetch)
    assert yf_sector.get_yf_sectors(['ZZZZ'], 'US') == {'ZZZZ': '-'}
    assert yf_sector.get_yf_sectors(['ZZZZ'], 'US') == {'ZZZZ': '-'}
    assert len(calls) == 1                                    # 세션 내 재시도 금지
    assert 'ZZZZ' not in json.loads(cache_file.read_text(encoding='utf-8'))


def test_렌더_조회는_상한까지만(tmp_path, monkeypatch):
    """유니버스 전체가 미캐시여도 첫 로딩이 멎지 않도록 한 번에 조회할 양을 자른다."""
    _setup(tmp_path, monkeypatch, {})
    monkeypatch.setattr(yf_sector, '_RENDER_FETCH_CAP', 3)
    calls = []

    def fake_fetch(ticker, market):
        calls.append(ticker)
        return '반도체'

    monkeypatch.setattr(yf_sector, 'fetch_label', fake_fetch)
    result = yf_sector.get_yf_sectors([f'T{i}' for i in range(10)], 'US')

    assert len(calls) == 3
    assert sum(1 for v in result.values() if v == '반도체') == 3
    assert sum(1 for v in result.values() if v == '-') == 7    # 나머지는 다음 렌더로 미룸


def test_resolve_sector_yfinance_우선():
    assert yf_sector.resolve_sector('방위산업', '우주·항공우주') == '방위산업'


def test_resolve_sector_yfinance_없으면_llm_대분류로_메운다():
    """yfinance는 코스닥 중소형주 industry를 안 주는 경우가 있다(1425 중 94종목)."""
    assert yf_sector.resolve_sector('', '반도체 제조 장비') == '반도체 제조 장비'
    assert yf_sector.resolve_sector('-', '원자력') == '원자력'


def test_resolve_sector_둘다_없으면_대시():
    assert yf_sector.resolve_sector('', '기타') == '-'
    assert yf_sector.resolve_sector('-', '-') == '-'
    assert yf_sector.resolve_sector('', '') == '-'


def test_cached_only_는_조회를_트리거하지_않는다(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, {'012450': '방위산업'})
    monkeypatch.setattr(yf_sector, 'fetch_label',
                        lambda *a: (_ for _ in ()).throw(AssertionError('fetch 호출됨')))
    assert yf_sector.get_yf_sectors_cached_only(['012450', '없는티커']) == {
        '012450': '방위산업', '없는티커': '-'}
