"""📢 안내 팝업(ui/notice.py) 게시 기간 가드 (#42 L12).

새 안내를 올리며 NOTICE_VERSION만 바꾸고 NOTICE_UNTIL(자동 팝업 마지막 날)을 빠뜨리면 UNTIL이
VERSION보다 앞이 되어 자동 팝업이 아예 뜨지 않는다. 시스템 python엔 Streamlit이 없어 모듈을
import할 수 없으므로 소스에서 두 상수를 읽어 비교한다.
"""
import ast
import pathlib
from datetime import date

NOTICE = pathlib.Path(__file__).resolve().parent.parent / 'ui' / 'notice.py'


def _notice_dates(src: str) -> tuple[date, date]:
    """(VERSION 날짜, UNTIL 날짜). NOTICE_VERSION = 'YYYY-MM-DD', NOTICE_UNTIL = date(Y, M, D) 형식."""
    consts = {}
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            consts[node.targets[0].id] = node.value
    version = date.fromisoformat(ast.literal_eval(consts['NOTICE_VERSION']))
    until = consts['NOTICE_UNTIL']
    assert isinstance(until, ast.Call) and until.func.id == 'date', 'NOTICE_UNTIL은 date(Y, M, D)로 둘 것'
    return version, date(*(ast.literal_eval(a) for a in until.args))


def test_notice_until_is_not_before_version():
    version, until = _notice_dates(NOTICE.read_text(encoding='utf-8'))
    assert until >= version, (f'NOTICE_UNTIL({until})이 NOTICE_VERSION({version})보다 앞 — '
                              '새 안내의 자동 팝업이 뜨지 않는다. 두 값을 함께 바꿀 것')


def test_guard_catches_version_bumped_without_until():
    # 가드가 실제로 잡는지 — VERSION만 올리고 UNTIL은 옛 게시 종료일로 남긴 경우
    stale = "NOTICE_VERSION = '2026-11-20'\nNOTICE_UNTIL = date(2026, 11, 19)\n"
    version, until = _notice_dates(stale)
    assert until < version
