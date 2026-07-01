import json
import subprocess
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_CACHE_FILE = _ROOT / 'sector_cache.json'


def _load_cache() -> dict:
    if _CACHE_FILE.exists():
        try:
            return json.loads(_CACHE_FILE.read_text(encoding='utf-8'))
        except Exception:
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    _CACHE_FILE.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2), encoding='utf-8'
    )


_CLAUDE_EXE = (
    r"C:\Users\PC\AppData\Roaming\npm\node_modules"
    r"\@anthropic-ai\claude-code\bin\claude.exe"
)

_SECTOR_PROMPT = (
    "이 회사를 주식 투자 테마 관점에서 분류해줘. "
    "구체적인 섹터 라벨 하나만 한국어로 답해. 절대 다른 말 하지 말고 라벨만. "
    "최대한 세분화해서 분류해. 예시 목록:\n"
    "\n"
    "[반도체]\n"
    "반도체 전공정 장비, 반도체 후공정 장비, 반도체 소재·부품, "
    "메모리 반도체, 시스템 반도체, 파운드리, 팹리스(반도체 설계), "
    "반도체 테스트·검사, AI GPU, HBM\n"
    "\n"
    "[2차전지]\n"
    "배터리 셀, 배터리 양극재, 배터리 음극재, 배터리 전해질, "
    "배터리 분리막, 배터리 부품·소재, 배터리 장비, 배터리 재활용\n"
    "\n"
    "[AI·SW·IT]\n"
    "AI 소프트웨어, AI 인프라, 데이터센터 인프라, 데이터센터 운영, "
    "클라우드 서비스, 네트워크 장비, 사이버보안, 핀테크, 게임, "
    "엔터테인먼트·미디어, IT 서비스·SI\n"
    "\n"
    "[바이오·헬스케어]\n"
    "바이오 신약, 의료기기, 진단·검사, CMO·CDMO, 의료 AI\n"
    "\n"
    "[자동차·모빌리티]\n"
    "완성차, 자동차 부품, 전기차 부품, 자율주행, 로보틱스\n"
    "\n"
    "[에너지·인프라]\n"
    "전력 인프라, 신재생에너지, 원전, 수소, 방위산업, 우주항공\n"
    "\n"
    "[디스플레이·광학]\n"
    "OLED 디스플레이, LCD 디스플레이, 디스플레이 부품·소재, 광학 부품\n"
    "\n"
    "[기타]\n"
    "철강·금속, 화학, 식품·음료, 유통·물류, 건설·부동산, 금융·보험, "
    "통신, 섬유·의복, 소재·화학\n"
)


def _classify(summary: str) -> str:
    result = subprocess.run(
        [_CLAUDE_EXE, '-p', _SECTOR_PROMPT + f"\n회사 설명:\n{summary[:800]}\n\n섹터 라벨:"],
        capture_output=True,
        timeout=60,
        stdin=subprocess.DEVNULL,
    )
    label = result.stdout.decode('utf-8', errors='replace').strip()
    label = label.split('\n')[0].strip().strip('"').strip("'").strip('*').strip('-').strip()
    if not label or len(label) > 40:
        raise ValueError(f'invalid label: {label[:60]!r}')
    return label


def _get_kr_name(ticker: str) -> str:
    """pykrx로 한국어 종목명 조회."""
    try:
        from pykrx import stock as pykrx_stock
        name = pykrx_stock.get_market_ticker_name(ticker)
        if name:
            return name
    except Exception:
        pass
    return ''


_KRX_SECTOR_MAP = {
    '음식료품': '식품·음료', '섬유의복': '섬유·의복', '종이목재': '종이·목재',
    '화학': '화학', '의약품': '바이오·제약', '비금속광물': '소재',
    '철강금속': '철강·금속', '기계': '기계', '전기전자': '전기·전자',
    '의료정밀': '의료·정밀기기', '운수장비': '자동차·운송장비',
    '유통업': '유통', '전기가스업': '유틸리티', '건설업': '건설',
    '운수창고업': '운송·물류', '통신업': '통신', '금융업': '금융',
    '은행': '은행', '증권': '증권', '보험': '보험',
    '서비스업': '서비스', '제조업': '제조',
}

_YF_INDUSTRY_MAP = {
    # 반도체
    'Semiconductors': '반도체 소재·부품',
    'Semiconductor Equipment & Materials': '반도체 전공정 장비',
    'Semiconductor Equipment': '반도체 전공정 장비',
    'Electronic Components': '반도체 소재·부품',
    'Electronic Equipment & Instruments': '반도체 테스트·검사',
    # 2차전지·에너지
    'Electrical Equipment & Parts': '전력 인프라',
    'Specialty Chemicals': '배터리 소재·화학',
    'Chemicals': '화학',
    # IT·소프트웨어
    'Software—Application': 'AI 소프트웨어',
    'Software—Infrastructure': 'IT 인프라·SW',
    'Information Technology Services': 'IT 서비스·SI',
    'Internet Content & Information': '인터넷·플랫폼',
    'Computer Hardware': 'IT 하드웨어',
    'Communication Equipment': '네트워크 장비',
    'Data Storage': '데이터센터 인프라',
    # 바이오·헬스케어
    'Biotechnology': '바이오 신약',
    'Drug Manufacturers—General': '바이오 신약',
    'Drug Manufacturers—Specialty & Generic': 'CMO·CDMO',
    'Medical Devices': '의료기기',
    'Medical Instruments & Supplies': '의료기기',
    'Diagnostics & Research': '진단·검사',
    'Health Information Services': '의료 AI',
    # 자동차·모빌리티
    'Auto Parts': '자동차 부품',
    'Auto Manufacturers': '완성차',
    'Auto & Truck Dealerships': '자동차 유통',
    # 산업재
    'Aerospace & Defense': '방위산업',
    'Industrial Machinery': '산업 기계',
    'Farm & Heavy Construction Machinery': '건설 기계',
    'Specialty Industrial Machinery': '반도체 전공정 장비',
    # 디스플레이
    'Consumer Electronics': 'OLED 디스플레이',
    # 기타
    'Steel': '철강·금속', 'Aluminum': '철강·금속',
    'Gold': '소재', 'Silver': '소재',
    'Oil & Gas E&P': '에너지', 'Oil & Gas Integrated': '에너지',
    'Banks—Regional': '은행', 'Banks—Diversified': '은행',
    'Insurance—Life': '보험', 'Insurance—Property & Casualty': '보험',
    'Capital Markets': '증권', 'Asset Management': '금융',
    'Entertainment': '엔터테인먼트·미디어',
    'Electronic Gaming & Multimedia': '게임',
    'Telecom Services': '통신',
    'Real Estate—General': '건설·부동산',
    'Residential Construction': '건설·부동산',
    'Grocery Stores': '식품·음료', 'Packaged Foods': '식품·음료',
    'Apparel Manufacturing': '섬유·의복',
}

_YF_SECTOR_MAP = {
    'Technology': 'IT·기술', 'Healthcare': '헬스케어', 'Financials': '금융',
    'Consumer Discretionary': '소비재', 'Consumer Staples': '필수소비재',
    'Industrials': '산업재', 'Energy': '에너지', 'Materials': '소재',
    'Real Estate': '부동산', 'Utilities': '유틸리티',
    'Communication Services': '통신·미디어',
}


def _fallback_sector(ticker: str, market: str) -> str:
    """Claude 분류 실패 시 pykrx/yfinance 섹터 데이터로 대체."""
    import yfinance as yf

    if market.startswith('KR'):
        try:
            from pykrx import stock as pykrx_stock
            from datetime import date
            today = date.today().strftime('%Y%m%d')
            df = pykrx_stock.get_market_sector_all(today, market='KOSPI' if market == 'KR_KOSPI' else 'KOSDAQ')
            if ticker in df.index:
                krx_sector = df.loc[ticker, '섹터'] if '섹터' in df.columns else ''
                if krx_sector and krx_sector in _KRX_SECTOR_MAP:
                    return _KRX_SECTOR_MAP[krx_sector]
                if krx_sector:
                    return krx_sector
        except Exception:
            pass

    suffix = '.KS' if market == 'KR_KOSPI' else ('.KQ' if market == 'KR_KOSDAQ' else '')
    try:
        info     = yf.Ticker(ticker + suffix).info
        industry = info.get('industry', '')
        sector   = info.get('sector', '')
        # industry 세분화 매핑 우선
        if industry in _YF_INDUSTRY_MAP:
            return _YF_INDUSTRY_MAP[industry]
        if sector in _YF_SECTOR_MAP:
            return _YF_SECTOR_MAP[sector]
        if industry:
            return industry
        if sector:
            return sector
    except Exception:
        pass

    return '기타'


def _build_summary(ticker: str, market: str) -> str:
    """분류에 쓸 설명 문자열 구성. 한국어명 + yfinance 설명 조합."""
    import yfinance as yf

    parts = []

    if market.startswith('KR'):
        kr_name = _get_kr_name(ticker)
        if kr_name:
            parts.append(f"회사명: {kr_name}")

    suffix = '.KS' if market == 'KR_KOSPI' else ('.KQ' if market == 'KR_KOSDAQ' else '')
    try:
        info = yf.Ticker(ticker + suffix).info
        biz = info.get('longBusinessSummary', '')
        if biz:
            parts.append(biz[:600])
        elif info.get('shortName'):
            parts.append(f"영문명: {info['shortName']}")
    except Exception:
        pass

    return '\n'.join(parts) if parts else ticker


def get_sectors(tickers: list, market: str) -> dict:
    """
    각 티커의 섹터 라벨 반환 {ticker: sector}.
    캐시에 없는 종목만 분류 후 저장.
    """
    cache = _load_cache()
    result = {}
    to_fetch = []

    for ticker in tickers:
        cache_key = f"{ticker}|{market}"
        if cache_key in cache and cache[cache_key] != '기타':
            result[ticker] = cache[cache_key]
        else:
            to_fetch.append(ticker)

    for ticker in to_fetch:
        sector = ''
        try:
            summary = _build_summary(ticker, market)
            sector = _classify(summary)
        except Exception:
            pass

        if not sector:
            sector = _fallback_sector(ticker, market)

        cache_key = f"{ticker}|{market}"
        cache[cache_key] = sector
        result[ticker] = sector

    if to_fetch:
        _save_cache(cache)

    return result
