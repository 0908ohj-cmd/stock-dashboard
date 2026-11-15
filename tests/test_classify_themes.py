"""#39 L4 — classify_themes는 실패가 있으면 0이 아닌 값을 반환한다.

전 종목이 실패해도 0을 반환해 run_batch_local.sh의 WARN 분기가 사실상 죽어 있었다.
claude CLI는 절대 실행하지 않는다 — classify_one을 대역으로 바꾼다.
"""
import sys

import pytest

from data import classification
from scripts import classify_themes as ct


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(classification, 'STORE_DIR', tmp_path / 'cls')
    classification._cache.clear()
    monkeypatch.setattr(ct, '_claude_bin', lambda: '/nonexistent/claude')
    monkeypatch.setattr(ct, 'target_tickers', lambda market: ['AAA', 'BBB'])
    monkeypatch.setattr(ct, '_name_hints', lambda market: {})
    monkeypatch.setattr(sys, 'argv', ['classify_themes.py', '--market', 'us', '--workers', '2'])

    def _install(fail: set):
        def _one(market, ticker, hint, binary):
            if ticker in fail:
                return ticker, None, 'timeout'
            return ticker, {'primaryTheme': 'UNCLASSIFIED', 'confidence': 'low', 'evidenceUrls': []}, ''
        monkeypatch.setattr(ct, 'classify_one', _one)
    yield _install
    classification._cache.clear()


def test_전_종목_실패면_0이_아닌_값을_반환한다(env):
    env({'AAA', 'BBB'})
    assert ct.main() != 0


def test_일부_실패도_0이_아닌_값을_반환한다(env):
    env({'BBB'})
    assert ct.main() != 0
    assert 'AAA' in classification.load_store('us')          # 성공분은 저장된다


def test_실패가_없으면_0(env):
    env(set())
    assert ct.main() == 0


def test_claude_CLI가_없으면_분류를_건너뛰고_0(env, monkeypatch):
    """CLI 없는 환경은 실패가 아니라 생략이다 (기존 동작 유지)."""
    monkeypatch.setattr(ct, '_claude_bin', lambda: None)
    assert ct.main() == 0
