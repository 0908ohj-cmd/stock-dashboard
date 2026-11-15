"""data/_atomic.atomic_write_text."""
import os

import pytest

from data import _atomic


def test_대상이_잠깐_잠겨_있으면_다시_시도한다(tmp_path, monkeypatch):
    """Windows는 다른 스레드가 대상 파일을 읽는 순간 os.replace가 PermissionError를 낸다
    (실행.bat 런처). 읽기는 금방 끝나므로 잠깐 기다렸다 다시 바꿔 끼운다."""
    target = tmp_path / 'US.json'
    target.write_text('old', encoding='utf-8')
    real_replace = os.replace
    calls = []

    def flaky_replace(src, dst):
        calls.append(dst)
        if len(calls) < 3:
            raise PermissionError(13, '다른 프로세스가 사용 중')
        return real_replace(src, dst)

    monkeypatch.setattr(_atomic.os, 'replace', flaky_replace)
    monkeypatch.setattr(_atomic.time, 'sleep', lambda s: None)

    _atomic.atomic_write_text(target, 'new')

    assert target.read_text(encoding='utf-8') == 'new'
    assert len(calls) == 3
    assert [p.name for p in tmp_path.iterdir()] == ['US.json']


def test_계속_잠겨_있으면_예외를_내고_임시_파일을_지운다(tmp_path, monkeypatch):
    target = tmp_path / 'US.json'
    target.write_text('old', encoding='utf-8')
    monkeypatch.setattr(_atomic.os, 'replace',
                        lambda src, dst: (_ for _ in ()).throw(PermissionError(13, '잠김')))
    monkeypatch.setattr(_atomic.time, 'sleep', lambda s: None)

    with pytest.raises(PermissionError):
        _atomic.atomic_write_text(target, 'new')

    assert target.read_text(encoding='utf-8') == 'old'
    assert [p.name for p in tmp_path.iterdir()] == ['US.json']
