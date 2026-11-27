"""bin/obs-scene-preview is what a placement gets judged from, so it has to put
each source where OBS would: at its scene position, at its scaled size, and
shifted left when a negative scale mirrors it."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[2]
loader = importlib.machinery.SourceFileLoader(
    "obs_scene_preview", str(REPO / "bin" / "obs-scene-preview")
)
spec = importlib.util.spec_from_loader(loader.name, loader)
preview = importlib.util.module_from_spec(spec)
loader.exec_module(preview)


def page() -> str:
    template = (REPO / "config" / "Tripbot.json.tmpl").read_text()
    collection = preview.load_scene(template, {"ONSCREENS_URL_BASE": "http://osc"})
    return preview.render(collection, still="/still")


def test_every_main_browser_source_is_an_iframe_on_its_page():
    template = (REPO / "config" / "Tripbot.json.tmpl").read_text()
    collection = preview.load_scene(template, {"ONSCREENS_URL_BASE": "http://osc"})
    urls = [
        s["settings"]["url"]
        for s in collection["sources"]
        if s["id"] == "browser_source"
    ]
    assert urls, "the template has browser sources"
    html = page()
    for url in urls:
        assert f'<iframe src="{url}"' in html


def test_sources_land_where_the_scene_puts_them():
    html = page()
    # leaderboard: unscaled, at its pos
    assert (
        'style="left:1500px;top:60px;width:400px;height:400px"><b>leaderboard' in html
    )
    # GPS: a 256px page mirrored and shrunk to 34px, so its box starts 34px left of pos.x
    assert 'style="left:1504.0px;top:1037px;width:34.0px;height:34.0px"><b>GPS' in html
    # the masks and the dashcam still are fitted to the full canvas
    assert (
        html.count("width:1920.0px;height:1080.0px")
        + html.count("width:1920px;height:1080px")
        >= 2
    )
    assert 'src="/still" width="1920" height="1080"' in html
