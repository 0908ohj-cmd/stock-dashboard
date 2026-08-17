"""
yfinance industry 기반 대분류(섹터) 매핑.

data/theme_classifier.py(LLM)와 축이 다르다 — 이쪽은 전통 산업 분류(방위산업/완성차/손해보험),
저쪽은 시장이 부르는 테마(자율드론 방어/전기차 풀라인업). 화면에서는 두 컬럼으로 나란히 쓴다.

LLM과 달리 yfinance는 배포된 Streamlit Cloud에서도 호출되므로, 신규 종목도 대분류는 바로 뜬다.
"""
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

_CACHE_FILE = Path(__file__).parent.parent / 'yf_sector_cache.json'

# 렌더 경로에서 한 번에 새로 조회할 상한 — 유니버스 전체가 미캐시여도 첫 로딩이 멎지 않게 한다.
# yfinance .info는 레이트 리밋이 빡빡하고(8워커로 전량 수집 시 45% 실패) 같은 예산을 시세 수집과
# 나눠 쓰므로, 렌더 경로는 신규 유입 몇 종목만 처리할 만큼으로 좁게 잡는다.
_RENDER_FETCH_CAP = 20
_RENDER_WORKERS = 3

# 이번 프로세스에서 조회를 시도한 티커 (실패분의 반복 재조회 억제)
_SESSION_ATTEMPTED: set = set()


def _load_cache() -> dict:
    if _CACHE_FILE.exists():
        try:
            return json.loads(_CACHE_FILE.read_text(encoding='utf-8'))
        except Exception:
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    _CACHE_FILE.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True), encoding='utf-8'
    )


def _normalize(label: str) -> str:
    """yfinance는 버전에 따라 'Software—Application'/'Software - Application'을 섞어 쓴다."""
    if not label:
        return ''
    return re.sub(r'\s*[-—–]\s*', ' - ', label).strip()


_INDUSTRY_MAP = {
    # 반도체·전자
    'Semiconductors': '반도체',
    'Semiconductor Equipment & Materials': '반도체 장비·소재',
    'Electronic Components': '전자부품',
    'Electronic Equipment & Instruments': '전자장비·계측',
    'Computer Hardware': 'IT 하드웨어',
    'Consumer Electronics': '전자·IT기기',
    'Communication Equipment': '통신장비',
    'Scientific & Technical Instruments': '정밀계측',
    'Solar': '태양광',
    # 소프트웨어·인터넷
    'Software - Application': '응용 소프트웨어',
    'Software - Infrastructure': '인프라 소프트웨어',
    'Information Technology Services': 'IT 서비스',
    'Internet Content & Information': '인터넷·플랫폼',
    'Electronic Gaming & Multimedia': '게임',
    # 바이오·헬스케어
    'Biotechnology': '바이오',
    'Drug Manufacturers - General': '제약',
    'Drug Manufacturers - Specialty & Generic': '제약(제네릭)',
    'Medical Devices': '의료기기',
    'Medical Instruments & Supplies': '의료기기',
    'Diagnostics & Research': '진단·연구',
    'Healthcare Plans': '건강보험',
    'Medical Care Facilities': '의료서비스',
    'Health Information Services': '의료 IT',
    'Pharmaceutical Retailers': '약국·유통',
    'Medical Distribution': '의약품 유통',
    # 산업재·방산
    'Aerospace & Defense': '방위산업',
    'Specialty Industrial Machinery': '산업기계',
    'Industrial Distribution': '산업재 유통',
    'Farm & Heavy Construction Machinery': '건설·농기계',
    'Building Products & Equipment': '건축자재',
    'Engineering & Construction': '건설·엔지니어링',
    'Infrastructure Operations': '인프라 운영',
    'Electrical Equipment & Parts': '전기장비',
    'Metal Fabrication': '금속가공',
    'Tools & Accessories': '공구·부품',
    'Pollution & Treatment Controls': '환경설비',
    'Waste Management': '폐기물관리',
    'Security & Protection Services': '보안서비스',
    'Staffing & Employment Services': '인력서비스',
    'Specialty Business Services': '기업서비스',
    'Consulting Services': '컨설팅',
    'Rental & Leasing Services': '렌탈·리스',
    'Conglomerates': '복합기업',
    'Airlines': '항공사',
    'Railroads': '철도',
    'Marine Shipping': '해운',
    'Trucking': '육상운송',
    'Integrated Freight & Logistics': '물류',
    'Airports & Air Services': '공항·항공서비스',
    # 자동차
    'Auto Manufacturers': '완성차',
    'Auto Parts': '자동차부품',
    'Auto & Truck Dealerships': '자동차 유통',
    'Recreational Vehicles': '레저차량',
    # 소재·화학
    'Specialty Chemicals': '정밀화학',
    'Chemicals': '화학',
    'Agricultural Inputs': '비료·농화학',
    'Steel': '철강',
    'Aluminum': '비철금속',
    'Copper': '비철금속',
    'Other Industrial Metals & Mining': '광물·자원',
    'Gold': '금·귀금속',
    'Silver': '금·귀금속',
    'Other Precious Metals & Mining': '금·귀금속',
    'Coking Coal': '석탄',
    'Thermal Coal': '석탄',
    'Uranium': '우라늄',
    'Paper & Paper Products': '제지',
    'Packaging & Containers': '포장재',
    'Lumber & Wood Production': '목재',
    'Building Materials': '건축소재',
    # 에너지
    'Oil & Gas E&P': '석유·가스 개발',
    'Oil & Gas Integrated': '종합 에너지',
    'Oil & Gas Midstream': '에너지 인프라',
    'Oil & Gas Refining & Marketing': '정유',
    'Oil & Gas Equipment & Services': '유전 서비스',
    'Oil & Gas Drilling': '시추',
    # 유틸리티
    'Utilities - Regulated Electric': '전력',
    'Utilities - Diversified': '유틸리티',
    'Utilities - Regulated Gas': '가스',
    'Utilities - Renewable': '신재생에너지',
    'Utilities - Regulated Water': '수도',
    'Utilities - Independent Power Producers': '민자발전',
    # 금융
    'Banks - Regional': '은행',
    'Banks - Diversified': '은행',
    'Capital Markets': '증권',
    'Asset Management': '자산운용',
    'Insurance - Property & Casualty': '손해보험',
    'Insurance - Life': '생명보험',
    'Insurance - Diversified': '보험',
    'Insurance - Reinsurance': '재보험',
    'Insurance - Specialty': '특종보험',
    'Insurance Brokers': '보험중개',
    'Financial Data & Stock Exchanges': '금융정보·거래소',
    'Credit Services': '여신·카드',
    'Financial Conglomerates': '금융지주',
    'Mortgage Finance': '모기지금융',
    'Shell Companies': '기타금융',
    # 소비재
    'Packaged Foods': '식품',
    'Beverages - Non-Alcoholic': '음료',
    'Beverages - Brewers': '주류',
    'Beverages - Wineries & Distilleries': '주류',
    'Confectioners': '제과',
    'Farm Products': '농축산물',
    'Food Distribution': '식품유통',
    'Grocery Stores': '식품소매',
    'Discount Stores': '대형마트',
    'Department Stores': '백화점',
    'Specialty Retail': '전문소매',
    'Internet Retail': '이커머스',
    'Apparel Retail': '의류소매',
    'Apparel Manufacturing': '의류제조',
    'Textile Manufacturing': '섬유',
    'Footwear & Accessories': '신발·액세서리',
    'Luxury Goods': '명품',
    'Household & Personal Products': '생활용품·화장품',
    'Personal Services': '개인서비스',
    'Home Improvement Retail': '홈퍼니싱 소매',
    'Furnishings, Fixtures & Appliances': '가구·가전',
    'Residential Construction': '주택건설',
    'Leisure': '레저용품',
    'Travel Services': '여행',
    'Lodging': '호텔',
    'Resorts & Casinos': '리조트·카지노',
    'Restaurants': '외식',
    'Gambling': '게임·베팅',
    'Tobacco': '담배',
    'Education & Training Services': '교육',
    # 미디어·통신
    'Telecom Services': '통신',
    'Entertainment': '엔터테인먼트',
    'Broadcasting': '방송',
    'Advertising Agencies': '광고',
    'Publishing': '출판',
    # 부동산
    'REIT - Industrial': '물류리츠',
    'REIT - Residential': '주거리츠',
    'REIT - Retail': '리테일리츠',
    'REIT - Office': '오피스리츠',
    'REIT - Healthcare Facilities': '헬스케어리츠',
    'REIT - Specialty': '특수리츠',
    'REIT - Hotel & Motel': '호텔리츠',
    'REIT - Diversified': '종합리츠',
    'REIT - Mortgage': '모기지리츠',
    'Real Estate Services': '부동산서비스',
    'Real Estate - Development': '부동산개발',
    'Real Estate - Diversified': '부동산',
}

# industry가 비거나 미매핑일 때 쓰는 상위 축
_SECTOR_MAP = {
    'Technology': 'IT·기술',
    'Healthcare': '헬스케어',
    'Financial Services': '금융',
    'Financial': '금융',
    'Consumer Cyclical': '경기소비재',
    'Consumer Defensive': '필수소비재',
    'Industrials': '산업재',
    'Energy': '에너지',
    'Basic Materials': '소재',
    'Real Estate': '부동산',
    'Utilities': '유틸리티',
    'Communication Services': '통신·미디어',
}


def map_label(sector: str, industry: str) -> str:
    """(sector, industry) → 한국어 대분류. 미매핑이면 industry 원문, 그것도 없으면 ''."""
    ind = _normalize(industry)
    if ind in _INDUSTRY_MAP:
        return _INDUSTRY_MAP[ind]
    sec = _normalize(sector)
    if sec in _SECTOR_MAP:
        return _SECTOR_MAP[sec]
    return ind or sec or ''


def _symbols(ticker: str, market: str) -> list:
    """조회할 yfinance 심볼 후보. KR은 KOSPI/KOSDAQ 접미사를 순서대로 시도한다."""
    if not ticker.isdigit():
        return [ticker]
    if market == 'KR_KOSDAQ':
        return [ticker + '.KQ', ticker + '.KS']
    return [ticker + '.KS', ticker + '.KQ']


def fetch_label(ticker: str, market: str) -> str:
    """yfinance에서 대분류 1건 조회. 실패하면 ''."""
    import yfinance as yf

    for sym in _symbols(ticker, market):
        try:
            info = yf.Ticker(sym).info
        except Exception:
            continue
        label = map_label(info.get('sector', ''), info.get('industry', ''))
        if label:
            return label
    return ''


def get_yf_sectors(tickers: list, market: str) -> dict:
    """{ticker: 대분류}. 캐시 우선, 미캐시분만 상한 내에서 병렬 조회한다.

    industry는 거의 바뀌지 않으므로 TTL을 두지 않는다 — 갱신은
    scripts/refresh_yf_sectors.py 몫.
    """
    cache = _load_cache()
    result = {}
    to_fetch = []

    for ticker in tickers:
        cached = cache.get(ticker)
        if cached:
            result[ticker] = cached
        elif ticker in _SESSION_ATTEMPTED:
            result[ticker] = '-'
        else:
            to_fetch.append(ticker)

    # 첫 로딩이 멎지 않도록 한 번에 조회할 양을 자른다 (나머지는 다음 렌더에서)
    batch, deferred = to_fetch[:_RENDER_FETCH_CAP], to_fetch[_RENDER_FETCH_CAP:]
    for ticker in deferred:
        result[ticker] = '-'

    if batch:
        _SESSION_ATTEMPTED.update(batch)
        fetched = {}
        with ThreadPoolExecutor(max_workers=_RENDER_WORKERS) as ex:
            futures = {ex.submit(fetch_label, t, market): t for t in batch}
            for future in as_completed(futures):
                ticker = futures[future]
                try:
                    fetched[ticker] = future.result()
                except Exception:
                    fetched[ticker] = ''
        dirty = False
        for ticker, label in fetched.items():
            if label:
                cache[ticker] = label
                dirty = True
            result[ticker] = label or '-'
        if dirty:
            try:
                _save_cache(cache)
            except Exception:
                pass  # 배포 환경의 읽기전용 FS 등 — 표시에는 영향 없음

    return result


def resolve_sector(yf_label: str, llm_theme: str) -> str:
    """섹터 컬럼의 최종 값.

    yfinance industry를 우선 쓰되, 없으면 LLM 대분류로 메운다 — yfinance는 코스닥 중소형주의
    industry를 아예 주지 않는 경우가 있다(1425종목 중 94종목). 둘 다 없으면 '-'.
    """
    if yf_label and yf_label != '-':
        return yf_label
    if llm_theme and llm_theme not in ('기타', '-'):
        return llm_theme
    return '-'


def get_yf_sectors_cached_only(tickers: list) -> dict:
    """캐시만 조회, 네트워크 미접촉."""
    cache = _load_cache()
    return {t: cache.get(t) or '-' for t in tickers}
