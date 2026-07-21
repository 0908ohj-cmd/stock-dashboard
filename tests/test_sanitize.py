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


def test_none_and_non_string_items():
    assert sanitize_tickers([None, 5930, 0]) == ['5930']
