from data.fetcher import sanitize_tickers, MAX_TICKERS_PER_MARKET


def test_valid_kr_us_tickers_pass():
    tickers = ['005930', 'AAPL', 'BRK.B', 'BF-B']
    assert sanitize_tickers(tickers) == tickers


def test_html_and_path_junk_filtered():
    dirty = ['<script>alert(1)</script>', '../../etc/passwd', 'AAPL', '', '  ', 'a b']
    assert sanitize_tickers(dirty) == ['AAPL']


def test_length_limit_10_chars():
    assert sanitize_tickers(['A' * 11]) == []
    assert sanitize_tickers(['A' * 10]) == ['A' * 10]


def test_cap_per_market():
    many = [f'{i:06d}' for i in range(MAX_TICKERS_PER_MARKET + 100)]
    assert len(sanitize_tickers(many)) == MAX_TICKERS_PER_MARKET


def test_whitespace_stripped():
    assert sanitize_tickers(['  005930  ']) == ['005930']


def test_non_string_items_dropped():
    # JSON 숫자 5930을 str()로 살리면 KR 코드 '005930'의 선행 0이 소실된
    # 오염 티커가 저장되므로, 비문자열은 변환 없이 버린다
    assert sanitize_tickers([None, 5930, 0, True]) == []


def test_punctuation_only_rejected():
    assert sanitize_tickers(['.', '..', '---', '.-.']) == []
