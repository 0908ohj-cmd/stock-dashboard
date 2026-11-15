import json
from unittest.mock import patch, MagicMock

import pytest

from data import theme_classifier as sector_classifier


@pytest.fixture
def fake_themes():
    return [
        {"name": "데이터센터 인프라", "description": "..."},
        {"name": "반도체설계", "description": "..."},
        {"name": "NAND 메모리", "description": "..."},
        {"name": "기타", "description": "..."},
    ]


# classify(렌더용 일괄 분류 — 캐시 TTL·override·재시도·저장)는 호출처가 없어져 지웠다(#46).
# 남은 부품은 scripts/refresh_sectors.py가 쓴다 — 1차 _classify_one, 2차 _resolve_misc. 재시도·캐시 저장
# 정책은 tests/test_refresh_sectors.py가 맡는다.

def _themes_file(tmp_path, monkeypatch, fake_themes):
    themes_file = tmp_path / "themes.json"
    themes_file.write_text(json.dumps({
        "known_themes": [{"name": t["name"], "description": t["description"]} for t in fake_themes],
        "ticker_overrides": {},
    }, ensure_ascii=False))
    monkeypatch.setattr(sector_classifier, "_THEMES_FILE", themes_file)
    return themes_file


def test_first_pass_returns_known_theme_and_detail(fake_themes):
    fake_proc = MagicMock(
        stdout='{"theme": "NAND 메모리", "detail": "AI 데이터센터 스토리지"}',
        returncode=0,
    )
    with patch.object(sector_classifier.subprocess, "run", return_value=fake_proc):
        result = sector_classifier._classify_one("WDC", None, fake_themes, "US")

    assert result == {"theme": "NAND 메모리", "detail": "AI 데이터센터 스토리지"}


def test_first_pass_invalid_json_is_total_failure(fake_themes):
    """1차 JSON 파싱 실패 → None (추측하지 않는다 — 호출부가 캐시에 넣지 않고 다음 회차 재시도)."""
    fake_proc = MagicMock(stdout="not json at all", returncode=0)
    with patch.object(sector_classifier.subprocess, "run", return_value=fake_proc):
        assert sector_classifier._classify_one("XYZ", None, fake_themes, "US") is None


def test_first_pass_unknown_theme_keeps_detail_as_misc(fake_themes):
    """대분류가 목록 밖이어도 detail이 있으면 '기타' + detail — 2차 resolve 대상."""
    fake_proc = MagicMock(stdout='{"theme": "없는 대분류", "detail": "핀테크 후보"}', returncode=0)
    with patch.object(sector_classifier.subprocess, "run", return_value=fake_proc):
        result = sector_classifier._classify_one("SOFI", None, fake_themes, "US")
    assert result == {"theme": "기타", "detail": "핀테크 후보"}


def test_resolve_misc_appends_new_theme(tmp_path, monkeypatch, fake_themes):
    themes_file = _themes_file(tmp_path, monkeypatch, fake_themes)
    current = {t: {"theme": "기타", "detail": "핀테크 후보"} for t in ("SOFI", "SOFI2")}
    second_proc = MagicMock(
        stdout=json.dumps([
            {"theme": "디지털금융·핀테크", "description": "디지털 금융 플랫폼", "members": ["SOFI", "SOFI2"]}
        ], ensure_ascii=False),
        returncode=0,
    )

    with patch.object(sector_classifier.subprocess, "run", return_value=second_proc):
        resolved = sector_classifier._resolve_misc(["SOFI", "SOFI2"], current, fake_themes)

    assert resolved["SOFI"] == {"theme": "디지털금융·핀테크", "detail": "핀테크 후보"}
    assert resolved["SOFI2"]["theme"] == "디지털금융·핀테크"
    # themes.json에 신규 대분류 추가됨
    names = [t["name"] for t in json.loads(themes_file.read_text())["known_themes"]]
    assert "디지털금융·핀테크" in names


def test_resolve_misc_groups_similar_tickers(tmp_path, monkeypatch, fake_themes):
    _themes_file(tmp_path, monkeypatch, fake_themes)
    current = {t: {"theme": "기타", "detail": "AI 서버 후보"} for t in ("SMCI", "DELL")}
    second_proc = MagicMock(
        stdout=json.dumps([
            {"theme": "AI 서버 제조", "description": "AI 서버 OEM/ODM", "members": ["SMCI", "DELL"]}
        ], ensure_ascii=False),
        returncode=0,
    )

    with patch.object(sector_classifier.subprocess, "run", return_value=second_proc):
        resolved = sector_classifier._resolve_misc(["SMCI", "DELL"], current, fake_themes)

    assert resolved["SMCI"]["theme"] == "AI 서버 제조"
    assert resolved["DELL"]["theme"] == "AI 서버 제조"


def test_resolve_misc_failure_returns_nothing(tmp_path, monkeypatch, fake_themes):
    """2차 응답이 JSON이 아니면 빈 결과 — 호출부가 1차 '기타' + detail로 캐시한다(자가치유)."""
    themes_file = _themes_file(tmp_path, monkeypatch, fake_themes)
    before = themes_file.read_text()
    with patch.object(sector_classifier.subprocess, "run", return_value=MagicMock(stdout="garbage", returncode=0)):
        resolved = sector_classifier._resolve_misc(["XYZ"], {"XYZ": {"theme": "기타", "detail": "모호 테마"}},
                                                   fake_themes)
    assert resolved == {}
    assert themes_file.read_text() == before


def test_resolve_misc_single_member_kept_as_other(tmp_path, monkeypatch, fake_themes):
    """1종목짜리 그룹은 known_themes에 안 들어가고 결과에도 없다 → 호출부가 '기타'로 캐시."""
    themes_file = _themes_file(tmp_path, monkeypatch, fake_themes)
    second_proc = MagicMock(
        stdout=json.dumps([
            {"theme": "이머징마켓 인덱스 펀드", "description": "...", "members": ["SOLO"]}
        ], ensure_ascii=False),
        returncode=0,
    )

    with patch.object(sector_classifier.subprocess, "run", return_value=second_proc):
        resolved = sector_classifier._resolve_misc(["SOLO"], {"SOLO": {"theme": "기타", "detail": "단독 테마"}},
                                                   fake_themes)

    assert resolved == {}
    names = [t["name"] for t in json.loads(themes_file.read_text())["known_themes"]]
    assert "이머징마켓 인덱스 펀드" not in names


def test_themes_json_atomic_write(tmp_path, monkeypatch):
    themes_file = tmp_path / "themes.json"
    themes_file.write_text(json.dumps({
        "known_themes": [{"name": "기존", "description": "x"}],
        "ticker_overrides": {},
    }, ensure_ascii=False))
    monkeypatch.setattr(sector_classifier, "_THEMES_FILE", themes_file)

    sector_classifier._append_known_themes([{"name": "신규", "description": "y"}])

    data = json.loads(themes_file.read_text())
    names = [t["name"] for t in data["known_themes"]]
    assert "신규" in names
    assert not themes_file.with_suffix(".json.tmp").exists()


def test_cache_save_keeps_previous_file_intact_while_writing(tmp_path, monkeypatch):
    from tests._write_probe import read_json_or_partial, split_writes

    cache_file = tmp_path / "theme_cache.json"
    old = {"WDC": {"theme": "NAND 메모리", "detail": "x", "updated": "2026-11-01T00:00:00"}}
    cache_file.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(sector_classifier, "_CACHE_FILE", cache_file)
    seen = []
    hits = split_writes(monkeypatch, tmp_path, on_half=lambda p: seen.append(read_json_or_partial(cache_file)))
    new = {**old, "NVDA": {"theme": "반도체설계", "detail": "y", "updated": "2026-11-13T00:00:00"}}

    sector_classifier._save_cache(new)

    assert hits
    assert seen == [old]
    assert json.loads(cache_file.read_text(encoding="utf-8")) == new
    assert [p.name for p in tmp_path.iterdir()] == ["theme_cache.json"]


# ── 시장 판정 (#45 L6) ─────────────────────────────────────────

def test_first_prompt_KR_시장이면_영숫자_코드도_한국_종목으로_알린다(fake_themes):
    """프롬프트가 '6자리 숫자 코드는 KRX, 그 외는 미국'이라 0015S0을 미국 종목으로 검색하게 했다."""
    prompt = sector_classifier._build_first_prompt("0015S0", None, fake_themes, "KR_KOSDAQ")
    assert "KRX(한국) 상장 종목" in prompt
    assert "미국" not in prompt


def test_first_prompt_US_시장이면_미국_종목으로_알린다(fake_themes):
    prompt = sector_classifier._build_first_prompt("NVDA", None, fake_themes, "US")
    assert "미국 상장 종목" in prompt
    assert "KRX" not in prompt


def test_classify_one_시장을_프롬프트에_싣는다(fake_themes):
    fake_proc = MagicMock(stdout='{"theme": "NAND 메모리", "detail": "x"}', returncode=0)
    with patch.object(sector_classifier.subprocess, "run", return_value=fake_proc) as run:
        sector_classifier._classify_one("0015G0", "가상종목", fake_themes, "KR_KOSPI")
    prompt = run.call_args.args[0][2]
    assert "0015G0 (가상종목)" in prompt and "KRX(한국) 상장 종목" in prompt
