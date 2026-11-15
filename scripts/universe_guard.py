"""10EMA 유니버스 빌더 공용 저장 가드 (#38).

빌더는 yfinance로 수백 종목을 청크·스레드로 받는데, 청크나 스레드가 죽으면 그 종목이
조용히 빠져 목록이 줄어든 채 정상 종료(exit 0)하고 배치가 그대로 커밋했다.
새 목록이 기존 파일보다 크게 줄면 부분 수집으로 보고 기존 파일을 지킨다.
"""
import pathlib

# 새 목록이 기존 파일 종목 수의 이 비율 미만이면 저장하지 않는다.
#   부분 실패 선례: 890c485 — launchd 파일 열기 한도 256에서 US 빌더가 21개만 남겼다(정상 119개, 18%).
#   평상 변동: git 이력(2026-08-13~11-12) 커밋 간 최대 감소가 KOSPI 66→49(74%), US 164→142(87%),
#   KOSDAQ 90→84(93%)라 60%면 시장 변화로 줄어드는 날은 통과하고 부분 실패는 걸린다.
# 진짜로 유니버스가 크게 줄어야 하는 날엔 매 회차 실패 알림이 간다 — 확인 후 기존 파일을 지우고
# 다시 돌리면 최초 실행으로 저장된다.
MIN_KEEP_RATIO = 0.6


def read_tickers(path: pathlib.Path) -> list:
    if not path.exists():
        return []
    return [t for t in path.read_text(encoding='utf-8').splitlines() if t.strip()]


def save_universe(path: pathlib.Path, tickers: list, label: str) -> bool:
    """가드를 통과하면 저장하고 True, 아니면 기존 파일을 그대로 두고 False.

    기존 파일이 없거나 비어 있으면(최초 실행) 비교 기준이 없으므로 비어 있지 않은 목록은 저장한다.
    """
    if not tickers:
        print(f'[FAIL] {label} 빌드 실패 — 기존 파일 유지')
        return False
    old = read_tickers(path)
    if old and len(tickers) < len(old) * MIN_KEEP_RATIO:
        print(f'[FAIL] {label} 새 목록 {len(tickers)}개가 기존 {len(old)}개의 '
              f'{MIN_KEEP_RATIO:.0%} 미만 — 부분 수집 의심, 기존 파일 유지')
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('\n'.join(tickers), encoding='utf-8')
    print(f'[OK] {path} 저장 완료 ({len(tickers)}개'
          + (f', 기존 {len(old)}개)' if old else ', 기존 파일 없음)'))
    return True
