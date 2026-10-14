"""The bundled airport list and the offline nearest-airport rule.

``nearest_airport_iata`` mirrors Community's ``nearest_airport``
(meshloom-community docs/contracts/identity.md, § IATA suggestion): no heliport,
no seaplane base, no metropolitan area code, no placeholder (XXX, LOC, AAA);
every other type is kept; the nearest by haversine, equal distances to the
smaller code.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from app.services import iata_airports
from app.services.iata_airports import Airport, airport_position, airports, nearest_airport_iata

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def fake_airports(monkeypatch):
    def install(*entries: Airport) -> None:
        monkeypatch.setattr(iata_airports, "airports", lambda: {a.iata: a for a in entries})
        iata_airports._suggestible.cache_clear()

    yield install
    iata_airports._suggestible.cache_clear()


class TestBundledList:
    def test_loads_with_types(self):
        loaded = airports()
        assert len(loaded) > 9000
        kinds = {a.kind for a in loaded.values()}
        assert {"large_airport", "heliport", "seaplane_base", "metro_area", "airport"} <= kinds
        assert loaded["JDP"].kind == "heliport"
        assert loaded["PAR"].kind == "metro_area"
        assert loaded["CDG"].kind == "large_airport"

    def test_records_its_source(self):
        payload = json.loads(iata_airports.AIRPORTS_FILE.read_text(encoding="utf-8"))
        assert len(payload["community_commit"]) == 40
        assert "OpenTravelData" in payload["license"]

    def test_central_paris_gets_le_bourget_not_the_heliport(self):
        # identity.md: "Central Paris gets LBG, not the Issy heliport JDP."
        assert nearest_airport_iata(48.86, 2.35) == "LBG"
        jdp = airports()["JDP"]
        assert nearest_airport_iata(jdp.lat, jdp.lon) != "JDP"

    @pytest.mark.parametrize("code", ["CXH", "PAR", "LON", "AAA"])
    def test_excluded_codes_are_never_suggested(self, code):
        entry = airports()[code]
        assert nearest_airport_iata(entry.lat, entry.lon) != code

    def test_position_lookup(self):
        assert airport_position("lys") == (airports()["LYS"].lat, airports()["LYS"].lon)
        assert airport_position("JDP") is not None  # placing a code takes any entry
        assert airport_position("QQQ") is None
        assert airport_position("") is None


class TestExclusionRule:
    def test_types(self, fake_airports):
        fake_airports(
            Airport("HEL", 10.0, 10.0, "heliport"),
            Airport("SEA", 10.0, 10.001, "seaplane_base"),
            Airport("MET", 10.0, 10.002, "metro_area"),
            Airport("OPT", 10.5, 10.0, "airport"),
            Airport("SML", 11.0, 10.0, "small_airport"),
        )
        assert nearest_airport_iata(10.0, 10.0) == "OPT"
        assert nearest_airport_iata(11.0, 10.0) == "SML"

    @pytest.mark.parametrize("placeholder", ["XXX", "LOC", "AAA"])
    def test_placeholders(self, fake_airports, placeholder):
        fake_airports(
            Airport(placeholder, 0.0, 20.0, "large_airport"),
            Airport("FAR", 5.0, 20.0, "medium_airport"),
        )
        assert nearest_airport_iata(0.0, 20.0) == "FAR"

    def test_tie_goes_to_the_smaller_code(self, fake_airports):
        fake_airports(
            Airport("ZZB", 1.0, 0.0, "small_airport"),
            Airport("ZZA", -1.0, 0.0, "small_airport"),
        )
        assert nearest_airport_iata(0.0, 0.0) == "ZZA"

    def test_nothing_loaded(self, fake_airports):
        fake_airports(Airport("HEL", 1.0, 1.0, "heliport"))
        assert nearest_airport_iata(1.0, 1.0) is None


class TestRefreshScript:
    @staticmethod
    def _script():
        path = REPO_ROOT / "scripts" / "data" / "refresh_iata_airports.py"
        spec = importlib.util.spec_from_file_location("refresh_iata_airports", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_build_mirrors_community_loading(self):
        script = self._script()
        snapshot = (
            "iata_code,latitude_deg,longitude_deg,type,source\n"
            "AAA,1.0,1.0,small_airport,ourairports\n"
            "AAA,2.0,2.0,large_airport,ourairports\n"
            "CDG,49.0,2.5,large_airport,ourairports\n"
            "ORY,48.7,2.4,large_airport,ourairports\n"
            "JDP,48.8,2.3,heliport,ourairports\n"
            "XX,0,0,small_airport,ourairports\n"
        )
        metro = (
            "# comment\n"
            "code,name,members,source\n"
            "PAR,Paris,CDG ORY ZZZ,wikivoyage\n"
            "CDG,Not a metro,ORY,wikivoyage\n"
            "XXX,Placeholder,CDG,wikivoyage\n"
            "NON,None,QQQ,wikivoyage\n"
        )
        payload = json.loads(script.build(snapshot, metro, "0" * 40))
        kinds = payload["kinds"]
        decoded = {
            code: (lat, lon, kinds[k]) for code, (lat, lon, k) in payload["airports"].items()
        }
        assert decoded == {
            "AAA": (2.0, 2.0, "large_airport"),
            "CDG": (49.0, 2.5, "large_airport"),
            "JDP": (48.8, 2.3, "heliport"),
            "ORY": (48.7, 2.4, "large_airport"),
            "PAR": (48.85, 2.45, "metro_area"),
        }
        assert list(payload["airports"]) == sorted(payload["airports"])
