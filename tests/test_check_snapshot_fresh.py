import json
from datetime import datetime, timedelta, timezone

from scripts import check_snapshot_fresh as cs

KST = timezone(timedelta(hours=9))


def _write(dirpath, name, fetched_at):
    (dirpath / name).write_text(
        json.dumps({'market': name, 'fetched_at': fetched_at, 'data': {}}),
        encoding='utf-8',
    )


def test_fresh_when_local_batch_just_ran(tmp_path):
    """로컬 배치가 3시간 전에 끝났으면 Action은 그대로 빠진다."""
    now = datetime(2026, 9, 16, 20, 0, tzinfo=KST)   # Action 폴백 시각
    _write(tmp_path, 'KR_KOSPI.json', '2026-09-16T16:50:00+09:00')
    _write(tmp_path, 'KR_KOSDAQ.json', '2026-09-16T16:50:00+09:00')

    assert cs.is_fresh('kr', now=now, ohlcv_dir=tmp_path) is True


def test_stale_when_local_batch_missed(tmp_path):
    """전날 스냅샷밖에 없으면 폴백이 수집한다."""
    now = datetime(2026, 9, 16, 20, 0, tzinfo=KST)
    _write(tmp_path, 'KR_KOSPI.json', '2026-09-15T16:50:00+09:00')
    _write(tmp_path, 'KR_KOSDAQ.json', '2026-09-15T16:50:00+09:00')

    assert cs.is_fresh('kr', now=now, ohlcv_dir=tmp_path) is False


def test_stale_when_one_market_lags(tmp_path):
    """한 시장만 뒤처져도 폴백이 돈다 — 부분 결손을 신선으로 보면 안 된다."""
    now = datetime(2026, 9, 16, 20, 0, tzinfo=KST)
    _write(tmp_path, 'KR_KOSPI.json', '2026-09-16T16:50:00+09:00')
    _write(tmp_path, 'KR_KOSDAQ.json', '2026-09-15T16:50:00+09:00')

    assert cs.is_fresh('kr', now=now, ohlcv_dir=tmp_path) is False


def test_missing_file_is_not_fresh(tmp_path):
    """파일이 없으면 신선으로 보지 않는다.

    판정 불능을 신선으로 처리하면 로컬이 죽었을 때 아무도 수집하지 않는다.
    """
    now = datetime(2026, 9, 16, 11, 0, tzinfo=KST)

    assert cs.is_fresh('us', now=now, ohlcv_dir=tmp_path) is False


def test_malformed_snapshot_is_not_fresh(tmp_path):
    """깨진 JSON도 신선으로 보지 않는다."""
    now = datetime(2026, 9, 16, 11, 0, tzinfo=KST)
    (tmp_path / 'US.json').write_text('{ not json', encoding='utf-8')

    assert cs.is_fresh('us', now=now, ohlcv_dir=tmp_path) is False


def test_missing_fetched_at_is_not_fresh(tmp_path):
    """fetched_at이 없는 스냅샷도 신선으로 보지 않는다."""
    now = datetime(2026, 9, 16, 11, 0, tzinfo=KST)
    (tmp_path / 'US.json').write_text(json.dumps({'market': 'US', 'data': {}}),
                                      encoding='utf-8')

    assert cs.is_fresh('us', now=now, ohlcv_dir=tmp_path) is False


def test_all_covers_both_markets(tmp_path):
    """--markets all은 KR 2개와 US 1개를 모두 본다."""
    now = datetime(2026, 9, 16, 11, 0, tzinfo=KST)
    _write(tmp_path, 'KR_KOSPI.json', '2026-09-16T09:00:00+09:00')
    _write(tmp_path, 'KR_KOSDAQ.json', '2026-09-16T09:00:00+09:00')
    _write(tmp_path, 'US.json', '2026-09-16T09:00:00+09:00')
    assert cs.is_fresh('all', now=now, ohlcv_dir=tmp_path) is True

    _write(tmp_path, 'US.json', '2026-09-14T09:00:00+09:00')
    assert cs.is_fresh('all', now=now, ohlcv_dir=tmp_path) is False


def test_boundary_uses_max_age_hours(tmp_path):
    """경계는 max_age_hours 초과일 때만 stale — 정확히 같은 값은 신선."""
    now = datetime(2026, 9, 16, 20, 0, tzinfo=KST)
    _write(tmp_path, 'US.json', '2026-09-16T12:00:00+09:00')   # 정확히 8시간 전

    assert cs.is_fresh('us', now=now, max_age_hours=8, ohlcv_dir=tmp_path) is True
    assert cs.is_fresh('us', now=now, max_age_hours=7, ohlcv_dir=tmp_path) is False
