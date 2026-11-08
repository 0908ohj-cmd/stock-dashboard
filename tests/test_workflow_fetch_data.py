import pathlib
import re

WORKFLOW = pathlib.Path(__file__).resolve().parent.parent / '.github' / 'workflows' / 'fetch-data.yml'


def test_every_cron_has_market_branch():
    """cron을 바꾸고 시장 결정 분기를 안 고치면 그 폴백은 매번 exit 1로 죽는다.

    2026-10-19 KR cron을 '50 7' → '10 9'로 옮기며 분기는 '50 7'로 남아,
    KR Action 폴백이 11월 초까지 매일 실패했다.
    """
    text = WORKFLOW.read_text(encoding='utf-8')
    crons = set(re.findall(r"-\s*cron:\s*'([^']+)'", text))
    branches = set(re.findall(r'github\.event\.schedule \}\}" = "([^"]+)"', text))

    assert crons, 'cron 항목을 못 찾음 — 정규식 갱신 필요'
    assert crons == branches
