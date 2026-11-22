"""The brand icon of the main console: five versions while the owner picks one (0 the first eye, 1-4 alternatives),
chosen with ?logo=0..4 or by a click on the icon, remembered in the browser, and used for the tab's icon too."""
import re

import pytest
from fastapi.testclient import TestClient

import ui.app as ui


@pytest.fixture
def client():
    return TestClient(ui.app)


def _page_and_script(client) -> tuple[str, str]:
    html = client.get("/").text
    src = re.search(r'<script src="(/static/v3/app\.js\?v=\d+)"></script>', html).group(1)
    return html, client.get(src).text


def _logos(js: str) -> list[dict]:
    """The LOGOS list as written in app.js: one {name, svg} per version, in order."""
    block = js.split("var LOGOS = [", 1)[1].split("\n  ];", 1)[0]
    return [{"name": n, "svg": s} for n, s in re.findall(r"\{ name: '([^']+)', svg: '([^']*)' \}", block)]


def test_the_icon_is_a_button_that_shows_the_next_version_and_the_tab_icon_follows(client):
    html, js = _page_and_script(client)
    assert re.search(r'<button class="mark-btn" id="brand-mark" type="button"[^>]*><svg class="mark" viewBox="0 0 24 24"', html)
    assert '<link rel="icon" id="favicon"' in html
    assert "$('brand-mark').addEventListener('click', nextLogo);" in js
    assert "link.href = 'data:image/svg+xml,'" in js                  # the tab's icon is the same drawing
    assert "showLogo();      // the tab's icon takes the new theme's colours" in js


def test_the_version_comes_from_the_address_then_the_browser_then_the_first_eye(client):
    _, js = _page_and_script(client)
    assert "params.get('logo')" in js and "store('v3-logo')" in js
    assert "if (/^[0-4]$/.test(asked)) { store('v3-logo', asked); return +asked; }" in js
    assert "return /^[0-4]$/.test(kept) ? +kept : 0;" in js


def test_five_versions_each_drawn_only_in_the_theme_colours(client):
    _, js = _page_and_script(client)
    logos = _logos(js)
    assert len(logos) == 5 and logos[0]["name"] == "Eye"
    assert len({l["name"] for l in logos}) == 5 and len({l["svg"] for l in logos}) == 5
    for l in logos:
        svg = l["svg"]
        assert svg and "<svg" not in svg and "<text" not in svg and "<style" not in svg, l["name"]
        assert not re.search(r"#[0-9a-fA-F]{3,6}\b|rgb\(|\bid=|style=|url\(", svg), l["name"]   # colour only from CSS
        assert 'class="acc' in svg, l["name"]                     # the accent part, in --iris
        # every fill or stroke is the ink, the accent's class, or none
        for colour in re.findall(r'(?:fill|stroke)="([^"]+)"', svg):
            assert colour in ("currentColor", "none"), (l["name"], colour)


def test_the_accent_classes_take_the_theme_accent(client):
    css = client.get("/static/v3/style.css").text
    assert ".mark .acc { fill: var(--iris); }" in css
    assert ".mark .acc-s { stroke: var(--iris); fill: none; }" in css
