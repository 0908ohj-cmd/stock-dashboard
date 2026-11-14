import json
import os
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from scripts import check_snapshot_fresh as cs

KST = timezone(timedelta(hours=9))
# 2026-09: 14=월 15=화 16=수 17=목 18=금 19=토 20=일


def _write(dirpath, name, fetched_at):
    (dirpath / name).write_text(
        json.dumps({'market': name, 'fetched_at': fetched_at, 'data': {}}),
        encoding='utf-8',
    )


def _kr(dirpath, fetched_at):
    _write(dirpath, 'KR_KOSPI.json', fetched_at)
    _write(dirpath, 'KR_KOSDAQ.json', fetched_at)


def _snap_dated(dirpath, name, fetched_at, last_td):
    (dirpath / name).write_text(json.dumps({'market': name, 'fetched_at': fetched_at,
                                            'last_trading_date': last_td, 'data': {}}), encoding='utf-8')


def _indices(dirpath, **last):
    (dirpath / 'indices.json').write_text(json.dumps({'market': 'indices', 'data': {
        k: {'dates': ['2026-11-05', v]} for k, v in last.items()}}), encoding='utf-8')


def test_us_stale_when_stocks_lag_index_same_batch(tmp_path):
    """2026-11-10 사례: 배치는 회차 뒤에 돌았지만 종목은 11/06, NASDAQ은 11/09 → 폴백이 다시 받아야 한다."""
    now = datetime(2026, 11, 10, 12, 0, tzinfo=KST)
    _snap_dated(tmp_path, 'US.json', '2026-11-10T10:53:15+09:00', '2026-11-06')
    _indices(tmp_path, NASDAQ='2026-11-09')
    assert cs.is_fresh('us', now=now, ohlcv_dir=tmp_path) is False
    _snap_dated(tmp_path, 'US.json', '2026-11-10T10:53:15+09:00', '2026-11-09')
    assert cs.is_fresh('us', now=now, ohlcv_dir=tmp_path) is True


def test_us_stale_when_index_lags_stocks(tmp_path):
    """2026-11-12 사례: ^IXIC rate limit으로 NASDAQ이 11/10에 남고 종목은 11/11 → 지수를 다시 받아야 한다."""
    now = datetime(2026, 11, 12, 9, 0, tzinfo=KST)
    _snap_dated(tmp_path, 'US.json', '2026-11-12T08:14:00+09:00', '2026-11-11')
    _indices(tmp_path, NASDAQ='2026-11-10')
    assert cs.is_fresh('us', now=now, ohlcv_dir=tmp_path) is False
    _indices(tmp_path, NASDAQ='2026-11-11')
    assert cs.is_fresh('us', now=now, ohlcv_dir=tmp_path) is True


def test_kr_stale_when_one_index_lags_its_stocks(tmp_path):
    now = datetime(2026, 11, 11, 18, 0, tzinfo=KST)
    for n in ('KR_KOSPI.json', 'KR_KOSDAQ.json'):
        _snap_dated(tmp_path, n, '2026-11-11T17:34:00+09:00', '2026-11-11')
    _indices(tmp_path, KOSPI='2026-11-11', KOSDAQ='2026-11-10')
    assert cs.is_fresh('kr', now=now, ohlcv_dir=tmp_path) is False


def test_kr_evening_nasdaq_one_day_behind_kospi_is_fresh(tmp_path):
    """KR 배치 뒤엔 KOSPI가 당일, NASDAQ·US 종목은 전날이다 — 시장마다 자기 지수와만 비교한다."""
    now = datetime(2026, 11, 12, 18, 0, tzinfo=KST)
    for n in ('KR_KOSPI.json', 'KR_KOSDAQ.json'):
        _snap_dated(tmp_path, n, '2026-11-12T17:34:00+09:00', '2026-11-12')
    _snap_dated(tmp_path, 'US.json', '2026-11-12T08:14:00+09:00', '2026-11-11')
    _indices(tmp_path, KOSPI='2026-11-12', KOSDAQ='2026-11-12', NASDAQ='2026-11-11')
    assert cs.is_fresh('all', now=now, ohlcv_dir=tmp_path) is True



def test_holiday_is_not_flagged_when_index_also_lacks_the_day(tmp_path):
    now = datetime(2026, 11, 10, 18, 0, tzinfo=KST)
    for n in ('KR_KOSPI.json', 'KR_KOSDAQ.json'):
        _snap_dated(tmp_path, n, '2026-11-10T17:34:00+09:00', '2026-11-09')
    _indices(tmp_path, KOSPI='2026-11-09', KOSDAQ='2026-11-09')      # 지수도 11/09 → 11/10은 휴장
    assert cs.is_fresh('kr', now=now, ohlcv_dir=tmp_path) is True


# ── 로컬 회차 계산 ───────────────────────────────────────────

def test_last_local_slot_kr():
    """KR 로컬 회차는 월~금 17:30."""
    assert cs.last_local_slot('kr', datetime(2026, 9, 16, 18, 10, tzinfo=KST)) == \
        datetime(2026, 9, 16, 17, 30, tzinfo=KST)
    # 17:30 이전이면 전날 회차
    assert cs.last_local_slot('kr', datetime(2026, 9, 16, 12, 0, tzinfo=KST)) == \
        datetime(2026, 9, 15, 17, 30, tzinfo=KST)
    # 자정을 넘긴 새벽(지연된 Action)이면 전날 회차
    assert cs.last_local_slot('kr', datetime(2026, 9, 17, 2, 30, tzinfo=KST)) == \
        datetime(2026, 9, 16, 17, 30, tzinfo=KST)
    # 주말이면 금요일 회차
    assert cs.last_local_slot('kr', datetime(2026, 9, 20, 10, 0, tzinfo=KST)) == \
        datetime(2026, 9, 18, 17, 30, tzinfo=KST)


def test_last_local_slot_us():
    """US 로컬 회차는 화~토 08:10."""
    assert cs.last_local_slot('us', datetime(2026, 9, 15, 8, 50, tzinfo=KST)) == \
        datetime(2026, 9, 15, 8, 10, tzinfo=KST)
    # 08:10 이전이면 직전 회차
    assert cs.last_local_slot('us', datetime(2026, 9, 16, 7, 0, tzinfo=KST)) == \
        datetime(2026, 9, 15, 8, 10, tzinfo=KST)
    # 월요일이면 토요일 회차
    assert cs.last_local_slot('us', datetime(2026, 9, 14, 13, 0, tzinfo=KST)) == \
        datetime(2026, 9, 12, 8, 10, tzinfo=KST)


@pytest.mark.parametrize('market', ['kr', 'us'])
def test_slots_match_launchd_plists(market):
    """게이트의 회차 표가 실제 launchd plist 스케줄과 같아야 한다.

    둘이 어긋나면 폴백이 로컬 성공을 못 알아보거나(매일 중복 수집) 로컬 실패를
    놓친다. US를 07:00 → 09:00으로 옮길 때 여러 파일이 따로 놀았던 전례가 있다.
    """
    import pathlib
    import plistlib

    plist = (pathlib.Path(cs.__file__).resolve().parent / 'launchd'
             / f'com.stockdashboard.batch.{market}.plist')
    slots = plistlib.loads(plist.read_bytes())['StartCalendarInterval']
    expected = cs.LOCAL_SLOTS[market]

    # launchd Weekday: 0/7=일, 1=월 … 6=토  →  파이썬 weekday: 0=월 … 6=일
    weekdays = {(s['Weekday'] - 1) % 7 for s in slots}
    assert weekdays == expected['weekdays']
    assert {(s['Hour'], s['Minute']) for s in slots} == \
        {(expected['hour'], expected['minute'])}


@pytest.mark.parametrize('market', ['kr', 'us'])
def test_slots_match_store_freshness_schedule(market):
    """앱의 stale 경고 기준(store._BATCH_SCHEDULE)도 같은 회차를 봐야 한다."""
    from data import store

    sched = store._BATCH_SCHEDULE[market.upper()]
    assert sched['weekdays'] == cs.LOCAL_SLOTS[market]['weekdays']
    assert sched['hour'] == cs.LOCAL_SLOTS[market]['hour']


# ── 판정 ──────────────────────────────────────────────────

def test_kr_fresh_when_local_covered_this_slot(tmp_path):
    """로컬이 17:34에 끝났으면 18:10 Action은 빠진다."""
    _kr(tmp_path, '2026-09-16T17:34:00+09:00')
    now = datetime(2026, 9, 16, 18, 10, tzinfo=KST)

    assert cs.is_fresh('kr', now=now, ohlcv_dir=tmp_path) is True


def test_kr_fresh_even_when_action_fires_hours_late(tmp_path):
    """Action이 몇 시간 늦게(여기선 새벽 2:30) 떠도 로컬 성공을 알아본다.

    GitHub cron은 실측 5~6.5시간 늦는다(2026-09, KR 06:45Z 예약 → 12:01~13:19Z).
    '수집 후 N시간 이내'로 판정하면 이 지연 때문에 로컬이 멀쩡한 날에도 폴백이
    다시 수집하고 '(Action 폴백)'이 매일 붙는다. 회차 기준이면 지연과 무관하다.
    """
    _kr(tmp_path, '2026-09-16T17:34:00+09:00')
    now = datetime(2026, 9, 17, 2, 30, tzinfo=KST)

    assert cs.is_fresh('kr', now=now, ohlcv_dir=tmp_path) is True


def test_kr_stale_when_local_missed_this_slot(tmp_path):
    """이번 회차(수 17:30) 이후 수집이 없으면 폴백이 수집한다."""
    _kr(tmp_path, '2026-09-15T17:34:00+09:00')   # 전날 것뿐
    now = datetime(2026, 9, 16, 18, 10, tzinfo=KST)

    assert cs.is_fresh('kr', now=now, ohlcv_dir=tmp_path) is False


def test_kr_stale_when_only_collected_before_slot(tmp_path):
    """같은 날이어도 회차 시각 전에 수집한 것은 이번 회차를 덮지 못한다."""
    _kr(tmp_path, '2026-09-16T15:00:00+09:00')
    now = datetime(2026, 9, 16, 18, 10, tzinfo=KST)

    assert cs.is_fresh('kr', now=now, ohlcv_dir=tmp_path) is False


def test_us_fresh_when_local_covered_this_slot(tmp_path):
    _write(tmp_path, 'US.json', '2026-09-15T08:14:00+09:00')

    assert cs.is_fresh('us', now=datetime(2026, 9, 15, 8, 50, tzinfo=KST),
                       ohlcv_dir=tmp_path) is True
    # 실측 US 지연 3~3.5시간, 더 늦어도(19:30) 마찬가지
    assert cs.is_fresh('us', now=datetime(2026, 9, 15, 19, 30, tzinfo=KST),
                       ohlcv_dir=tmp_path) is True


def test_us_stale_when_local_missed_this_slot(tmp_path):
    _write(tmp_path, 'US.json', '2026-09-12T08:14:00+09:00')   # 토요일 것뿐
    now = datetime(2026, 9, 15, 8, 50, tzinfo=KST)              # 화요일 폴백

    assert cs.is_fresh('us', now=now, ohlcv_dir=tmp_path) is False


def test_stale_when_one_file_lags(tmp_path):
    """한 파일만 뒤처져도 폴백이 돈다 — 부분 결손을 신선으로 보면 안 된다."""
    _write(tmp_path, 'KR_KOSPI.json', '2026-09-16T17:34:00+09:00')
    _write(tmp_path, 'KR_KOSDAQ.json', '2026-09-15T17:34:00+09:00')
    now = datetime(2026, 9, 16, 18, 10, tzinfo=KST)

    assert cs.is_fresh('kr', now=now, ohlcv_dir=tmp_path) is False


def test_all_checks_each_market_against_its_own_slot(tmp_path):
    now = datetime(2026, 9, 16, 18, 10, tzinfo=KST)
    _kr(tmp_path, '2026-09-16T17:34:00+09:00')
    _write(tmp_path, 'US.json', '2026-09-16T08:14:00+09:00')
    assert cs.is_fresh('all', now=now, ohlcv_dir=tmp_path) is True

    _write(tmp_path, 'US.json', '2026-09-15T08:14:00+09:00')
    assert cs.is_fresh('all', now=now, ohlcv_dir=tmp_path) is False


# ── 판정 불능은 신선이 아니다 ─────────────────────────────────

def test_missing_file_is_not_fresh(tmp_path):
    """판정 불능을 신선으로 보면 로컬이 죽었을 때 아무도 수집하지 않는다."""
    assert cs.is_fresh('us', now=datetime(2026, 9, 15, 13, 0, tzinfo=KST),
                       ohlcv_dir=tmp_path) is False


def test_malformed_snapshot_is_not_fresh(tmp_path):
    (tmp_path / 'US.json').write_text('{ not json', encoding='utf-8')
    assert cs.is_fresh('us', now=datetime(2026, 9, 15, 13, 0, tzinfo=KST),
                       ohlcv_dir=tmp_path) is False


def test_missing_fetched_at_is_not_fresh(tmp_path):
    (tmp_path / 'US.json').write_text(json.dumps({'market': 'US', 'data': {}}),
                                      encoding='utf-8')
    assert cs.is_fresh('us', now=datetime(2026, 9, 15, 13, 0, tzinfo=KST),
                       ohlcv_dir=tmp_path) is False


def test_future_fetched_at_is_not_fresh(tmp_path):
    """fetched_at이 한참 미래면(맥 시계 오류) 신선으로 보지 않는다.

    신선으로 처리하면 시계가 고쳐질 때까지 폴백이 영영 돌지 않는다.
    """
    _write(tmp_path, 'US.json', '2026-09-18T09:00:00+09:00')
    assert cs.is_fresh('us', now=datetime(2026, 9, 15, 13, 0, tzinfo=KST),
                       ohlcv_dir=tmp_path) is False


def test_small_clock_skew_is_tolerated(tmp_path):
    """두 기계 시계가 몇 분 어긋난 정도는 허용한다."""
    _write(tmp_path, 'US.json', '2026-09-15T13:03:00+09:00')   # 3분 앞섬
    assert cs.is_fresh('us', now=datetime(2026, 9, 15, 13, 0, tzinfo=KST),
                       ohlcv_dir=tmp_path) is True


# ── git ref에서 읽기 (Action이 커밋 직전 원격을 다시 볼 때) ────────

def test_reads_from_git_ref(tmp_path):
    """--ref로 작업본이 아니라 git ref(예: origin/main)의 스냅샷을 판정한다.

    Action은 자기가 방금 쓴 작업본이 아니라, 그 사이 로컬이 올렸을지 모르는
    원격 상태를 봐야 한다.
    """
    env = {**os.environ, 'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t',
           'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@t'}
    repo = tmp_path / 'repo'
    ohlcv = repo / 'data' / 'ohlcv'
    ohlcv.mkdir(parents=True)
    subprocess.run(['git', 'init', '-q', '-b', 'main'], cwd=repo, env=env, check=True)
    _kr(ohlcv, '2026-09-16T17:34:00+09:00')              # 커밋된 상태: 로컬이 수집함
    subprocess.run(['git', 'add', '.'], cwd=repo, env=env, check=True)
    subprocess.run(['git', 'commit', '-q', '-m', 's'], cwd=repo, env=env, check=True)
    _kr(ohlcv, '2026-09-15T00:00:00+09:00')              # 작업본은 옛 값으로 덮음

    now = datetime(2026, 9, 16, 18, 10, tzinfo=KST)
    assert cs.is_fresh('kr', now=now, ohlcv_dir=ohlcv) is False              # 작업본
    assert cs.is_fresh('kr', now=now, ohlcv_dir=ohlcv, ref='main') is True   # ref
