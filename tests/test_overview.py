"""The system overview page is generated from the code; these tests catch it going stale or losing a section."""
import html
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _builder():
    spec = importlib.util.spec_from_file_location("build_system_overview", ROOT / "docs" / "build_system_overview.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_page_builds_and_names_every_fault_kind_and_domain():
    from sim.world import CATEGORIES, COUNTRIES, PLATFORMS, CHANNELS
    from ui.domains import DOMAINS
    page = _builder().build()
    for kind in ("payment_failure", "stockout_surge", "search_regression", "ad_budget_exhausted", "promo", "data_delay"):
        assert kind in page, kind
    for _, label, metrics in DOMAINS:
        assert html.escape(label) in page and all(m in page for m in metrics)
    assert str(len(COUNTRIES) * len(PLATFORMS) * len(CATEGORIES) * len(CHANNELS)) in page
    assert "brain-architecture.html" in page and "ground_truth" in page


def test_every_scenario_in_the_table_exists_in_the_code():
    from sim import inject
    b = _builder()
    for name, *_ in b.SCENARIOS:
        assert hasattr(inject, name), f"{name} is in the overview but not in sim/inject.py"


def test_the_committed_page_has_the_same_facts_as_the_code():
    """Not byte for byte (line counts move with every edit); the page must mention the current segment and row counts."""
    committed = (ROOT / "docs" / "system-overview.html").read_text(encoding="utf-8")
    from sim import world
    segments = len(world.COUNTRIES) * len(world.PLATFORMS) * len(world.CATEGORIES) * len(world.CHANNELS)
    assert f"<b>{segments}</b>segments per hour" in committed and f"<b>{28 * 24 * segments:,}</b>rows in 28 days" in committed
