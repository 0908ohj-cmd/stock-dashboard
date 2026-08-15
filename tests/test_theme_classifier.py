import json
from datetime import datetime, timedelta
from pathlib import Path
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


def test_cache_hit_new_format_returns_both_fields(tmp_path, monkeypatch, fake_themes):
    cache_file = tmp_path / "theme_cache.json"
    cache_file.write_text(json.dumps({
        "WDC": {
            "theme": "NAND 메모리",
            "detail": "AI 데이터센터 스토리지",
            "updated": datetime.now().isoformat(),
        }
    }))
    monkeypatch.setattr(sector_classifier, "_CACHE_FILE", cache_file)

    with patch.object(sector_classifier, "_classify_one") as mock_one:
        result = sector_classifier.classify(["WDC"], fake_themes)

    assert result == {"WDC": {"theme": "NAND 메모리", "detail": "AI 데이터센터 스토리지"}}
    mock_one.assert_not_called()


def test_cache_hit_old_format_treated_as_miss(tmp_path, monkeypatch, fake_themes):
    cache_file = tmp_path / "theme_cache.json"
    cache_file.write_text(json.dumps({
        "WDC": {"theme": "AI 데이터센터 스토리지", "updated": datetime.now().isoformat()}
    }))
    monkeypatch.setattr(sector_classifier, "_CACHE_FILE", cache_file)

    with patch.object(sector_classifier, "_classify_one", return_value={"theme": "NAND 메모리", "detail": "AI 데이터센터 스토리지"}) as mock_one:
        result = sector_classifier.classify(["WDC"], fake_themes)

    mock_one.assert_called_once()
    assert result["WDC"]["theme"] == "NAND 메모리"


def test_override_skips_llm(tmp_path, monkeypatch, fake_themes):
    cache_file = tmp_path / "theme_cache.json"
    monkeypatch.setattr(sector_classifier, "_CACHE_FILE", cache_file)

    with patch.object(sector_classifier, "_classify_one") as mock_one:
        result = sector_classifier.classify(
            ["CRDO"], fake_themes, overrides={"CRDO": "반도체설계"}
        )

    assert result == {"CRDO": {"theme": "반도체설계", "detail": "반도체설계"}}
    mock_one.assert_not_called()


def test_cache_expired_triggers_reclassify(tmp_path, monkeypatch, fake_themes):
    cache_file = tmp_path / "theme_cache.json"
    old_ts = (datetime.now() - timedelta(days=8)).isoformat()
    cache_file.write_text(json.dumps({
        "WDC": {"theme": "NAND 메모리", "detail": "AI 데이터센터 스토리지", "updated": old_ts}
    }))
    monkeypatch.setattr(sector_classifier, "_CACHE_FILE", cache_file)

    with patch.object(sector_classifier, "_classify_one", return_value={"theme": "NAND 메모리", "detail": "메모리 반도체"}) as mock_one:
        result = sector_classifier.classify(["WDC"], fake_themes)

    mock_one.assert_called_once()
    assert result["WDC"]["detail"] == "메모리 반도체"


def test_first_pass_returns_known_theme_and_detail(tmp_path, monkeypatch, fake_themes):
    cache_file = tmp_path / "theme_cache.json"
    monkeypatch.setattr(sector_classifier, "_CACHE_FILE", cache_file)

    fake_proc = MagicMock(
        stdout='{"theme": "NAND 메모리", "detail": "AI 데이터센터 스토리지"}',
        returncode=0,
    )
    with patch.object(sector_classifier.subprocess, "run", return_value=fake_proc):
        result = sector_classifier.classify(["WDC"], fake_themes)

    assert result["WDC"] == {"theme": "NAND 메모리", "detail": "AI 데이터센터 스토리지"}
    saved = json.loads(cache_file.read_text())
    assert saved["WDC"]["theme"] == "NAND 메모리"
    assert saved["WDC"]["detail"] == "AI 데이터센터 스토리지"


def test_first_pass_invalid_json_total_failure_not_cached(tmp_path, monkeypatch, fake_themes):
    """1차 JSON 파싱 실패(빈 detail) → 추측 없이 '기타', 캐시 미저장(자가치유 재시도)."""
    cache_file = tmp_path / "theme_cache.json"
    monkeypatch.setattr(sector_classifier, "_CACHE_FILE", cache_file)

    fake_proc = MagicMock(stdout="not json at all", returncode=0)
    with patch.object(sector_classifier.subprocess, "run", return_value=fake_proc):
        result = sector_classifier.classify(["XYZ"], fake_themes)

    assert result["XYZ"] == {"theme": "기타", "detail": ""}
    # 자가치유: 캐시에 저장하지 않아 다음 회차 재분류
    assert not cache_file.exists()


def test_first_pass_retries_once_before_giving_up(tmp_path, monkeypatch, fake_themes):
    """1차 분류가 일시적으로 실패하면 1회 재시도한다."""
    cache_file = tmp_path / "theme_cache.json"
    monkeypatch.setattr(sector_classifier, "_CACHE_FILE", cache_file)

    good = {"theme": "NAND 메모리", "detail": "메모리 반도체"}
    with patch.object(
        sector_classifier, "_classify_one", side_effect=[None, good]
    ) as mock_one:
        result = sector_classifier.classify(["WDC"], fake_themes)

    assert mock_one.call_count == 2
    assert result["WDC"] == good


def test_total_failure_empty_detail_not_resolved(tmp_path, monkeypatch, fake_themes):
    """1차 완전 실패(detail 확보 실패)는 2차 resolve로 넘기지 않는다.

    티커 철자만 보고 추측한 대분류가 캐시에 박히는 FROG 오분류 회귀 방지.
    (FROG=소프트웨어인데 '헬스케어·의약'으로 저장됐던 버그)
    """
    cache_file = tmp_path / "theme_cache.json"
    monkeypatch.setattr(sector_classifier, "_CACHE_FILE", cache_file)

    # 1차: 항상 파싱 실패(빈 detail) → None. 재시도해도 None.
    first_proc = MagicMock(stdout="not json at all", returncode=0)
    with patch.object(sector_classifier.subprocess, "run", return_value=first_proc):
        with patch.object(sector_classifier, "_resolve_misc") as mock_resolve:
            result = sector_classifier.classify(["FROG"], fake_themes)

    # 2차 resolve는 호출조차 되지 않아야 함 (추측 원천 차단)
    mock_resolve.assert_not_called()
    assert result["FROG"] == {"theme": "기타", "detail": ""}
    # 캐시에 저장되지 않아 다음 회차 재시도(자가치유)
    assert not cache_file.exists()


def test_resolve_misc_appends_new_theme(tmp_path, monkeypatch, fake_themes):
    cache_file = tmp_path / "theme_cache.json"
    themes_file = tmp_path / "themes.json"
    themes_file.write_text(json.dumps({
        "known_themes": [{"name": t["name"], "description": t["description"]} for t in fake_themes],
        "ticker_overrides": {},
        "theme_to_weekly_sectors": {},
    }, ensure_ascii=False))

    monkeypatch.setattr(sector_classifier, "_CACHE_FILE", cache_file)
    monkeypatch.setattr(sector_classifier, "_THEMES_FILE", themes_file)

    # 1차: 대분류는 못 정했지만 detail 후보는 확보 → 2차 resolve 대상
    first_proc = MagicMock(stdout='{"theme": "기타", "detail": "핀테크 후보"}', returncode=0)
    second_proc = MagicMock(
        stdout=json.dumps([
            {"theme": "디지털금융·핀테크", "description": "디지털 금융 플랫폼", "members": ["SOFI", "SOFI2"]}
        ], ensure_ascii=False),
        returncode=0,
    )

    with patch.object(
        sector_classifier.subprocess, "run",
        side_effect=[first_proc, first_proc, second_proc],
    ):
        result = sector_classifier.classify(["SOFI", "SOFI2"], fake_themes)

    assert result["SOFI"]["theme"] == "디지털금융·핀테크"
    assert result["SOFI2"]["theme"] == "디지털금융·핀테크"
    # themes.json에 신규 대분류 추가됨
    saved_themes = json.loads(themes_file.read_text())
    names = [t["name"] for t in saved_themes["known_themes"]]
    assert "디지털금융·핀테크" in names
    # 캐시에 신규 대분류로 저장
    saved_cache = json.loads(cache_file.read_text())
    assert saved_cache["SOFI"]["theme"] == "디지털금융·핀테크"


def test_resolve_misc_groups_similar_tickers(tmp_path, monkeypatch, fake_themes):
    cache_file = tmp_path / "theme_cache.json"
    themes_file = tmp_path / "themes.json"
    themes_file.write_text(json.dumps({
        "known_themes": [{"name": t["name"], "description": t["description"]} for t in fake_themes],
        "ticker_overrides": {},
        "theme_to_weekly_sectors": {},
    }, ensure_ascii=False))
    monkeypatch.setattr(sector_classifier, "_CACHE_FILE", cache_file)
    monkeypatch.setattr(sector_classifier, "_THEMES_FILE", themes_file)

    first_proc = MagicMock(stdout='{"theme": "기타", "detail": "AI 서버 후보"}', returncode=0)
    second_proc = MagicMock(
        stdout=json.dumps([
            {"theme": "AI 서버 제조", "description": "AI 서버 OEM/ODM", "members": ["SMCI", "DELL"]}
        ], ensure_ascii=False),
        returncode=0,
    )

    with patch.object(
        sector_classifier.subprocess, "run",
        side_effect=[first_proc, first_proc, second_proc],
    ):
        result = sector_classifier.classify(["SMCI", "DELL"], fake_themes)

    assert result["SMCI"]["theme"] == "AI 서버 제조"
    assert result["DELL"]["theme"] == "AI 서버 제조"


def test_resolve_misc_failure_caches_as_other(tmp_path, monkeypatch, fake_themes):
    cache_file = tmp_path / "theme_cache.json"
    themes_file = tmp_path / "themes.json"
    themes_file.write_text(json.dumps({
        "known_themes": [{"name": t["name"], "description": t["description"]} for t in fake_themes],
        "ticker_overrides": {},
        "theme_to_weekly_sectors": {},
    }, ensure_ascii=False))
    monkeypatch.setattr(sector_classifier, "_CACHE_FILE", cache_file)
    monkeypatch.setattr(sector_classifier, "_THEMES_FILE", themes_file)

    # 1차에서 detail 후보 확보(→ 2차 resolve 대상), 2차는 실패
    first_proc = MagicMock(stdout='{"theme": "기타", "detail": "모호 테마"}', returncode=0)
    second_proc = MagicMock(stdout="garbage", returncode=0)

    with patch.object(
        sector_classifier.subprocess, "run",
        side_effect=[first_proc, second_proc],
    ):
        result = sector_classifier.classify(["XYZ"], fake_themes)

    assert result["XYZ"]["theme"] == "기타"
    # 2차 실패해도 detail 후보가 있었으므로 '기타'로 캐시 저장 (자가치유)
    saved = json.loads(cache_file.read_text())
    assert saved["XYZ"]["theme"] == "기타"


def test_resolve_misc_single_member_kept_as_other(tmp_path, monkeypatch, fake_themes):
    """1종목짜리 그룹은 known_themes에 안 들어가고 '기타'로 캐시."""
    cache_file = tmp_path / "theme_cache.json"
    themes_file = tmp_path / "themes.json"
    themes_file.write_text(json.dumps({
        "known_themes": [{"name": t["name"], "description": t["description"]} for t in fake_themes],
        "ticker_overrides": {},
        "theme_to_weekly_sectors": {},
    }, ensure_ascii=False))
    monkeypatch.setattr(sector_classifier, "_CACHE_FILE", cache_file)
    monkeypatch.setattr(sector_classifier, "_THEMES_FILE", themes_file)

    first_proc = MagicMock(stdout='{"theme": "기타", "detail": "단독 테마"}', returncode=0)
    # 2차 resolve가 1종목짜리 그룹 반환
    second_proc = MagicMock(
        stdout=json.dumps([
            {"theme": "이머징마켓 인덱스 펀드", "description": "...", "members": ["SOLO"]}
        ], ensure_ascii=False),
        returncode=0,
    )

    with patch.object(sector_classifier.subprocess, "run", side_effect=[first_proc, second_proc]):
        result = sector_classifier.classify(["SOLO"], fake_themes)

    # 1종목짜리는 known_themes에 추가 안 됨
    saved_themes = json.loads(themes_file.read_text())
    names = [t["name"] for t in saved_themes["known_themes"]]
    assert "이머징마켓 인덱스 펀드" not in names

    # 자가치유로 '기타' 캐시
    assert result["SOLO"]["theme"] == "기타"
    saved_cache = json.loads(cache_file.read_text())
    assert saved_cache["SOLO"]["theme"] == "기타"


def test_themes_json_atomic_write(tmp_path, monkeypatch):
    themes_file = tmp_path / "themes.json"
    themes_file.write_text(json.dumps({
        "known_themes": [{"name": "기존", "description": "x"}],
        "ticker_overrides": {},
        "theme_to_weekly_sectors": {},
    }, ensure_ascii=False))
    monkeypatch.setattr(sector_classifier, "_THEMES_FILE", themes_file)

    sector_classifier._append_known_themes([{"name": "신규", "description": "y"}])

    data = json.loads(themes_file.read_text())
    names = [t["name"] for t in data["known_themes"]]
    assert "신규" in names
    assert not themes_file.with_suffix(".json.tmp").exists()
