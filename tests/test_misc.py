from pathlib import Path

import pytest

from animedl.config import load_settings
from animedl.models import SeriesStatus
from animedl.naming import episode_basename, format_episode, safe_name
from animedl.scraping import detect_status, extract_js_value, lang_from_label, page_title


def test_safe_name():
    assert safe_name('Re:Zero / Kara: "Hajimeru"?') == "Re Zero Kara Hajimeru"
    assert safe_name("CON") == "_CON"
    assert safe_name("...") == "sin titulo"


def test_episode_formatting():
    assert format_episode(5) == "05"
    assert format_episode(170) == "170"
    assert format_episode(12.5) == "12.5"
    assert episode_basename(
        "{series}/Season {season:02d}/{series} - S{season:02d}E{episode}",
        series="Black Clover",
        season=1,
        number=7,
    ) == Path("Black Clover/Season 01/Black Clover - S01E07")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("En emisión", SeriesStatus.AIRING),
        ("Estado: En emision", SeriesStatus.AIRING),
        ("Finalizado", SeriesStatus.FINISHED),
        ("Concluido", SeriesStatus.FINISHED),
        ("Por estrenar", SeriesStatus.UPCOMING),
        ("Anime", SeriesStatus.UNKNOWN),
    ],
)
def test_detect_status(text, expected):
    assert detect_status(text) == expected


def test_lang_from_label():
    assert lang_from_label("LATINO") == "lat"
    assert lang_from_label("Sub Español") == "sub"
    assert lang_from_label("Castellano") == "cast"
    assert lang_from_label("Servidor 1") is None


def test_extract_js_value_handles_semicolons_inside_strings():
    html = 'var videos = {"SUB":[{"code":"https://x/e/a;b"}]}; var other = 1;'
    assert extract_js_value(html, "videos") == {"SUB": [{"code": "https://x/e/a;b"}]}
    assert extract_js_value(html, "missing") is None


def test_page_title_strips_site_suffix():
    from animedl.scraping import make_soup

    soup = make_soup("<title>Ver Black Clover Online Sub Español - JKAnime</title>")
    assert page_title(soup, ["h1"]) == "Black Clover"


def test_settings_file_and_env(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text('download_dir = "/mnt/anime"\nlanguages = ["lat", "sub"]\nmax_attempts = 9\n')
    monkeypatch.setenv("ANIMEDL_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("ANIMEDL_SERVERS", "mega, okru")
    s = load_settings(cfg)
    assert s.download_dir == Path("/mnt/anime")
    assert s.languages == ["lat", "sub"]
    assert s.max_attempts == 3
    assert s.servers == ["mega", "okru"]


def test_settings_reject_unknown_keys(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('dowload_dir = "/typo"\n')
    with pytest.raises(ValueError, match="dowload_dir"):
        load_settings(cfg)
