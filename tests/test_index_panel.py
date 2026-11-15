"""지수 현황 패널 'PP 거래 가능' 조건 — ui/index_panel._pp_ok (#46 L15).

ui 모듈이라 streamlit이 없는 최소 환경에선 건너뛴다. CI는 requirements.txt를 설치해 돈다.
"""
import numpy as np
import pandas as pd
import pytest

pytest.importorskip('streamlit')
from ui.index_panel import _pp_ok  # noqa: E402


def _idx(closes):
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame({'Open': c, 'High': c * 1.01, 'Low': c * 0.99, 'Close': c, 'Volume': 1e6},
                        index=pd.bdate_range('2026-01-01', periods=len(c)))


def test_10EMA가_21EMA_위이고_둘_다_오르면_가능():
    assert _pp_ok(_idx(np.linspace(100, 130, 60)))


def test_10EMA가_21EMA_아래면_불가():
    assert not _pp_ok(_idx(np.linspace(130, 100, 60)))


def test_기울기는_전일_대비_하루로_본다():
    """10EMA > 21EMA이고 5일 전보다는 둘 다 높아도, 오늘 하루 10EMA가 꺾이면 불가 (docstring의 '5일 기준'은 틀렸다)."""
    assert not _pp_ok(_idx(list(np.linspace(100, 130, 59)) + [127.0]))


def test_이력_26일_미만은_불가():
    assert not _pp_ok(_idx(np.linspace(100, 130, 25)))
