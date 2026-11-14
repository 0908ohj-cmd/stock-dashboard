"""주도섹터 탭 화면 보조 로직 — 기준일 선택.

화면(ui/sector_leadership.py)이 쓰는 결정 규칙만 모았다. Streamlit 무의존이라 UI 없이 테스트한다.
"""


def pin_asof(picked: str, latest: str) -> str | None:
    """사용자가 기준일 상자에서 고른 날 → 세션에 기억할 값.

    최신일을 고르면 None(= 최신 따라가기)이다. 그 사람은 '그 날짜'가 아니라 '최신'을 원한 것이라,
    다음 배치가 들어오면 같이 넘어가야 한다.
    """
    return None if picked == latest else picked


def resolve_asof(opts: list, pinned: str | None) -> str:
    """이번 실행의 기준일. opts는 최신순 선택지, pinned는 pin_asof가 남긴 값.

    직접 고른 과거 날짜만 유지하고, 그 밖에는 매 실행 최신일(opts[0])을 쓴다. '세션에 남은 값이
    선택지에 있으면 유지'하면 새 배치가 들어와도 옛 최신일이 선택지에 남아 있어 전날 기준에 고정된다(#42 M10).
    """
    return pinned if pinned in opts else opts[0]
