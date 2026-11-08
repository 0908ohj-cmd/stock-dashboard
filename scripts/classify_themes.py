#!/usr/bin/env python3
"""종목 투자테마 분류 배치 — claude CLI(Haiku) + 웹검색, 고정 테마 목록 안에서만 분류.

사용법:
  python scripts/classify_themes.py --market us                 # 기록 없는 종목만 (신규 편입)
  python scripts/classify_themes.py --market kr --scope unclassified   # 미분류 재시도
  python scripts/classify_themes.py --market us --scope all     # 전체 재분류 (사용자 확정은 보존)
  python scripts/classify_themes.py --market us --tickers SMCI LASR

배치(run_batch_local.sh)가 매 회차 --scope missing으로 돌려 새로 들어온 종목을 같은 기준으로 분류한다.
claude CLI가 없는 환경에서는 아무것도 바꾸지 않고 0으로 끝난다.
"""
import argparse
import json
import pathlib
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data import classification as cls  # noqa: E402

MODEL = 'claude-haiku-5-5'
TIMEOUT_SEC = 240
RETRIES = 3
RETRY_WAIT_SEC = 30
SAVE_EVERY = 10
_EXCLUDE = {'QQQ', 'IWM', 'IXIC', 'KOSPI', 'KOSDAQ', 'NASDAQ'}

_TICKER_FILES = {
    'us': ['us.tickers', 'us_10ema.tickers'],
    'kr': ['kospi.tickers', 'kosdaq.tickers', 'kospi_10ema.tickers', 'kosdaq_10ema.tickers'],
}


def target_tickers(market: str) -> list:
    saved = ROOT / 'data' / 'saved'
    out = []
    if market == 'us':
        uni = json.loads((ROOT / 'data/universe/us_innovation_210.json').read_text(encoding='utf-8'))
        out += [s['ticker'] for s in uni['stocks']]
    for name in _TICKER_FILES[market]:
        p = saved / name
        if p.exists():
            out += [x.strip() for x in p.read_text(encoding='utf-8').splitlines() if x.strip()]
    return [t for t in dict.fromkeys(out) if t not in _EXCLUDE]


def _name_hints(market: str) -> dict:
    hints = {}
    if market == 'kr':
        try:
            hints.update(json.loads((ROOT / 'data/kr_names.json').read_text(encoding='utf-8')))
        except Exception:
            pass
    else:
        uni = json.loads((ROOT / 'data/universe/us_innovation_210.json').read_text(encoding='utf-8'))
        hints.update({s['ticker']: s['exchange'] for s in uni['stocks'] if s.get('exchange')})
    return hints


_US_NOTES = """- 같은 티커가 다른 회사에 재사용·변경된 경우가 있다. 반드시 현재 그 티커로 거래되는 회사명을 확인하라.
- 레이저 기업은 방산 고출력 레이저(DEFENSE_DIRECTED_ENERGY)와 데이터센터 광통신(OPTICAL_INTERCONNECT)을 매출 기준으로 구분하라.
- eVTOL·전기항공기는 ELECTRIC_AVIATION이지 로봇이 아니다. 금속 3D 프린팅은 ADDITIVE_MANUFACTURING이다.
- AI 서버·랙 공급사는 AI_DC_HARDWARE이고, GPU 클라우드를 직접 운영·임대하는 회사만 AI_NEOCLOUD다.
- 비트코인 채굴과 AI/HPC를 함께 하는 회사는 최근 매출·계약 비중이 큰 쪽을 주력으로, 다른 쪽을 보조 태그로 둔다.
- 전기차 완성차는 CONSUMER_OTHER를 주력으로 하고 자율주행은 보조 태그로만 둘 수 있다."""

_KR_NOTES = """- 6자리 숫자는 한국거래소 종목코드다. 반드시 회사명을 확인하라.
- 지주회사는 핵심 자회사 사업이 매출 대부분이면 그 테마, 아니면 HOLDING_OTHER.
- 반도체는 칩 제조(SEMI_MEMORY), 장비(SEMI_EQUIPMENT), 소재·소모품(SEMI_MATERIALS_PARTS), 기판(SEMI_SUBSTRATE_PCB)을 구분하라."""


def build_prompt(market: str, ticker: str, hint: str | None) -> str:
    tax = cls.load_taxonomy(market)
    lines = '\n'.join(f"- {t['code']} ({t['label']}): {t['desc']}" for t in tax['themes'])
    # 'NN'처럼 짧은 티커를 자리 표시로 오해하지 않도록 "티커 심볼"이라고 못박는다
    if market == 'us':
        who = f'미국 상장 종목(티커 심볼 "{ticker}"' + (f', 거래소 {hint}' if hint else '') + ')'
        notes = _US_NOTES
    else:
        who = f'한국 상장 종목(종목코드 "{ticker}"' + (f', 종목명 {hint}' if hint else '') + ')'
        notes = _KR_NOTES
    return f"""당신은 기업 사업 분류 담당자다. {who}의 실제 주력 사업을 웹 검색으로 확인하고 아래 고정 테마 목록 안에서만 분류하라.

근거 우선순위: (a) 회사 공식 사이트·IR·최근 사업보고서(10-K, DART) > (b) 매출 세그먼트·주요 고객 > (c) 공식 산업분류(GICS 등) > (d) 기사·시장 인식.
주가가 비슷하게 움직인다는 이유나 테마 기사만으로 분류하지 마라. 확인이 안 되면 primaryTheme을 UNCLASSIFIED로 두고 confidence를 low로 하라.
evidenceUrls에는 이번에 실제로 열어 보거나 검색 결과에서 확인한 URL만 넣어라. 지어낸 URL은 금지다.

주의:
{notes}

테마 목록 (code만 사용, 새 테마 금지):
{lines}

출력은 아래 JSON 한 개만. 다른 텍스트 금지.
{{"companyName": "회사 정식명", "businessSummary": "주력 사업 1~2문장 한국어", "detail": "시장에서 부르는 2~4단어 한국어 테마", "primaryTheme": "CODE", "secondaryThemes": ["CODE"], "confidence": "high|medium|low", "why": "주력 테마를 고른 근거 1~2문장 (매출 비중·제품 등)", "evidenceUrls": ["https://..."]}}"""


def _claude_bin() -> str | None:
    """launchd처럼 PATH가 짧은 환경도 고려한다 (CLAUDE_BIN → PATH → ~/.local/bin)."""
    import os
    for cand in (os.environ.get('CLAUDE_BIN'), shutil.which('claude'),
                 os.path.expanduser('~/.local/bin/claude')):
        if cand and os.path.exists(cand):
            return cand
    return None


def _extract_json(text: str) -> dict | None:
    dec = json.JSONDecoder()
    found = None
    i = text.find('{')
    while i != -1:
        try:
            obj, end = dec.raw_decode(text, i)
            if isinstance(obj, dict) and 'primaryTheme' in obj:
                found = obj
            i = text.find('{', end)
        except ValueError:
            i = text.find('{', i + 1)
    return found


def classify_one(market: str, ticker: str, hint: str | None, binary: str) -> tuple[str, dict | None, str]:
    """일시적 실패(CLI 업데이트로 실행 파일 부재, 사용량 한도 거절, 타임아웃)는 대기 후 재시도."""
    prompt = build_prompt(market, ticker, hint)
    err = ''
    for attempt in range(RETRIES):
        if attempt:
            time.sleep(RETRY_WAIT_SEC * attempt)
        try:
            proc = subprocess.run(
                [_claude_bin() or binary, '-p', '--model', MODEL,
                 '--allowedTools', 'WebSearch', 'WebFetch',
                 '--output-format', 'text'],
                input=prompt, capture_output=True, text=True, encoding='utf-8',
                timeout=TIMEOUT_SEC,
            )
        except subprocess.TimeoutExpired:
            err = 'timeout'
            continue
        except OSError as e:
            err = f'실행 실패: {e}'
            continue
        raw = _extract_json(proc.stdout or '')
        if raw is not None:
            return ticker, raw, ''
        # 로그인 만료 예고 경고는 stderr 첫 줄에 항상 붙으므로 원인 파악용으로 뒷부분을 남긴다
        err = f'JSON 없음 (rc={proc.returncode}) {(proc.stdout or proc.stderr or "")[-200:]}'
    return ticker, None, err


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--market', choices=['us', 'kr'], required=True)
    ap.add_argument('--scope', choices=['missing', 'unclassified', 'all'], default='missing')
    ap.add_argument('--tickers', nargs='*')
    ap.add_argument('--workers', type=int, default=6)
    ap.add_argument('--limit', type=int, default=0, help='이번 실행 최대 종목 수 (0=제한 없음)')
    ap.add_argument('--allow-draft', action='store_true', help='초안(-draft) 테마 목록으로도 실행')
    args = ap.parse_args()

    market = args.market
    version = cls.load_taxonomy(market)['version']
    if version.endswith('-draft') and not args.allow_draft:
        print(f'[{market}] 테마 목록 {version}은 확인 전 초안 — 분류 생략 (--allow-draft로 강제)')
        return 0

    binary = _claude_bin()
    if not binary:
        print('claude CLI 없음 — 분류 생략')
        return 0

    records = dict(cls.load_store(market))
    universe = args.tickers or target_tickers(market)
    if args.tickers:
        todo = list(args.tickers)
    elif args.scope == 'missing':
        todo = [t for t in universe if t not in records]
    elif args.scope == 'unclassified':
        todo = [t for t in universe if t not in records
                or (records[t].get('primaryTheme') == cls.UNCLASSIFIED and not records[t].get('overrideByUser'))]
    else:
        todo = [t for t in universe if not (records.get(t) or {}).get('overrideByUser')]
    if args.limit:
        todo = todo[:args.limit]
    print(f'[{market}] 대상 {len(todo)}개 (전체 {len(universe)}, 기록 {len(records)})', flush=True)
    if not todo:
        return 0

    hints = _name_hints(market)
    done = accepted = 0
    errors = {}
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(classify_one, market, t, hints.get(t), binary) for t in todo]
        for fut in as_completed(futs):
            ticker, raw, err = fut.result()
            done += 1
            if raw is None:
                errors[ticker] = err
                print(f'  [{done}/{len(todo)}] {ticker} 실패: {err}', flush=True)
                continue
            rec = cls.normalize(market, raw, method=f'llm:{MODEL}+websearch')
            if cls.apply_auto(market, ticker, rec, records):
                accepted += rec['primaryTheme'] != cls.UNCLASSIFIED
            print(f"  [{done}/{len(todo)}] {ticker} → {rec['primaryTheme']} ({rec['confidence']})"
                  + (f" 게이트: {', '.join(rec['gateReasons'])}" if rec['gateReasons'] else ''), flush=True)
            if done % SAVE_EVERY == 0:
                cls.save_store(market, records)
    cls.save_store(market, records)
    print(f'[{market}] 완료 {done}개 — 승인 {accepted}, 미분류 {done - accepted - len(errors)}, '
          f'실패 {len(errors)} ({time.time() - t0:.0f}초)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
