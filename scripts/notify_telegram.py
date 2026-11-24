#!/usr/bin/env python3
"""배치의 수집 이상·폴백 실패를 텔레그램으로 보낸다.

맥 배치가 0이 아닌 코드로 끝나면 stockEdge job_wrapper가 이미 알린다. 여기서 보내는 것은 그 알림이
닿지 않는 경우다 — 배치는 성공으로 끝났지만 수집 도구가 실패한 경우(2026-11-23: KRX 비밀번호 만료로
pyKRX가 실패했는데 yfinance로 넘어가 아무 알림이 없었다)와 GitHub 폴백의 실패.

표준 라이브러리만 쓴다 — 의존성 설치가 실패한 Action에서도 보낼 수 있어야 한다.
알림 실패가 배치를 실패시키면 안 되므로 예외를 내지 않고, 토큰이 든 URL을 출력하지 않는다.

사용법: python3 scripts/notify_telegram.py "메시지"   (항상 종료 코드 0)
환경 변수: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID — 없으면 보내지 않는다.
"""
import os
import sys
import urllib.parse
import urllib.request

API_BASE = 'https://api.telegram.org'
MAX_TEXT = 4096     # 텔레그램 메시지 길이 한도


def send(text: str, timeout: float = 10) -> bool:
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    chat = os.environ.get('TELEGRAM_CHAT_ID', '').strip()
    if not token or not chat:
        return False
    where = 'GitHub 폴백' if os.environ.get('GITHUB_ACTIONS') == 'true' else '맥'
    body = urllib.parse.urlencode({
        'chat_id': chat,
        'text': f'📉 stock-dashboard 배치 ({where}) {text}'[:MAX_TEXT],
        'disable_web_page_preview': 'true',
    }).encode()
    try:
        req = urllib.request.Request(f'{API_BASE}/bot{token}/sendMessage', data=body)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except Exception as e:
        print(f'[telegram] 발송 실패({type(e).__name__})', file=sys.stderr)
        return False


if __name__ == '__main__':
    if len(sys.argv) > 1:
        send(sys.argv[1])
    sys.exit(0)
