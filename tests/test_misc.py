from pathlib import Path

import pytest

from animedl.config import load_settings
from animedl.models import SeriesStatus
from animedl.naming import NameParts, episode_basename, format_episode, movie_basename, safe_name
from animedl.scraping import detect_status, extract_js_value, lang_from_label, page_title


def test_safe_name():
    assert safe_name('Re:Zero / Kara: "Hajimeru"?') == "Re Zero Kara Hajimeru"
    assert safe_name("CON") == "_CON"
    assert safe_name("...") == "sin titulo"


def test_episode_formatting():
    assert format_episode(5) == "05"
    assert format_episode(170) == "170"
    parts = NameParts("Black Clover", 2017, "{tmdb-73223}")
    template = "Series/{series}{ids}/Season {season:02d}/{series} - S{season:02d}E{episode}"
    assert episode_basename(template, parts, season=2, number=7) == Path(
        "Series/Black Clover (2017) {tmdb-73223}/Season 02/Black Clover (2017) - S02E07"
    )
    # half episodes go to the specials season
    assert episode_basename(template, parts, season=1, number=12.5) == Path(
        "Series/Black Clover (2017) {tmdb-73223}/Season 00/Black Clover (2017) - S00E125"
    )
    assert episode_basename(template, NameParts("Re:Zero"), season=1, number=1) == Path(
        "Series/Re Zero/Season 01/Re Zero - S01E01"
    )


def test_movie_naming():
    parts = NameParts("A Silent Voice", 2016, "{tmdb-378064}")
    template = "Peliculas/{series}{ids}/{series}{ids}"
    assert movie_basename(template, parts) == Path(
        "Peliculas/A Silent Voice (2016) {tmdb-378064}/A Silent Voice (2016) {tmdb-378064}"
    )
    assert movie_basename(template, parts, part=2).name.endswith("{tmdb-378064} - pt2")


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
