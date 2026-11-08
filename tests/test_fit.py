"""Scope-fit rules: these labels are what users see, so each rule gets a known-answer test."""
from yfinder import fit
from yfinder.geo import coverage_fraction, parse_resolution_m

NEW_HAVEN = [-72.998, 41.246, -72.86, 41.351]
KENYA = [34.0, -0.5, 35.0, 0.8]
SCOPE = {"bbox": NEW_HAVEN, "time_start": "2025-06-01", "time_end": "2025-08-31", "spatial_resolution_m": 1,
         "unit_of_analysis": "water site"}


def test_resolution_too_coarse_for_habitats():
    s2 = {"bbox": [-180, -90, 180, 90], "t0": 2015, "t1": None, "res_m": 10}
    f = fit.assess(s2, SCOPE)
    assert f["resolution"]["label"] == "poor"
    assert f["overall"] == "poor"
    assert "10 m" in f["resolution"]["reason"]


def test_resolution_partial_and_good():
    assert fit.assess({"res_m": 3}, SCOPE)["resolution"]["label"] == "partial"     # 3 m vs 1 m need
    assert fit.assess({"res_m": 0.3}, SCOPE)["resolution"]["label"] == "good"


def test_us_dataset_does_not_cover_kenya():
    f = fit.assess({"region": "US", "res_m": 0.6}, {"bbox": KENYA})
    assert f["extent"]["label"] == "poor"


def test_connecticut_dataset_covers_new_haven():
    assert fit.assess({"region": "Connecticut"}, SCOPE)["extent"]["label"] == "good"


def test_partial_extent():
    half = [-72.93, 41.2, -72.0, 41.4]  # covers the eastern half of the study box
    assert fit.check_extent({"bbox": half}, SCOPE)["label"] == "partial"


def test_time_ends_before_window():
    f = fit.check_time({"t0": 2000, "t1": 2015}, {"time_start": "2020", "time_end": "2023"})
    assert f["label"] == "poor" and "before" in f["reason"]


def test_time_partial_overlap():
    f = fit.check_time({"t0": 2018, "t1": 2021}, {"time_start": "2015", "time_end": "2023"})
    assert f["label"] == "partial"


def test_ongoing_record_serves_near_future_window():
    f = fit.check_time({"t0": 1979, "t1": None}, {"time_start": "2027-06-01", "time_end": "2027-08-31"})
    assert f["label"] == "good" and "Ongoing" in f["reason"]


def test_same_month_window_is_covered_by_multiyear_record():
    f = fit.check_time({"t0": 2017, "t1": None}, {"time_start": "2023-06-01", "time_end": "2023-06-30"})
    assert f["label"] == "good", f
    f2 = fit.check_time({"t0": 2017, "t1": 2023}, {"time_start": "2023-06-01", "time_end": "2023-06-30"})
    assert f2["label"] == "good", f2


def test_exact_end_date_is_not_stretched_to_year_end():
    f = fit.check_time({"t0": "2020-01-01", "t1": "2023-01-31"}, {"time_start": "2023-07-01", "time_end": "2023-08-31"})
    assert f["label"] == "poor", f


def test_point_records_do_not_auto_pass_resolution():
    assert fit.check_resolution({"unit": "Monitor point"}, {"spatial_resolution_m": 1})["label"] == "unknown"


def test_unknown_when_metadata_missing():
    f = fit.assess({}, SCOPE)
    assert f["overall"] == "unknown"


def test_reporting_unit_hierarchy():
    tract_scope = {"unit_of_analysis": "census tract"}
    assert fit.check_resolution({"unit": "Block group, tract"}, tract_scope)["label"] == "good"
    assert fit.check_resolution({"unit": "County"}, tract_scope)["label"] == "poor"
    assert fit.check_resolution({"unit": "Town"}, tract_scope)["label"] == "partial"


def test_unit_rank_prefers_finest():
    assert fit.unit_rank("Block group and custom areas") == 2
    assert fit.unit_rank("Block") == 1
    assert fit.unit_rank("Tract, county") == 3


def test_access_labels():
    assert fit.check_access({"access_class": "yale", "access": "Yale license"})["label"] == "yale"
    assert fit.check_access({"access_class": "open"})["label"] == "open"


def test_parse_resolution_text():
    assert parse_resolution_m("Sentinel-2 imagery at 10 m resolution") == 10
    assert parse_resolution_m("gridded at 1 km") == 1000
    assert abs(parse_resolution_m("0.25 degree grid") - 27830) < 1
    assert parse_resolution_m("rotated 30 degrees") is None
    assert parse_resolution_m(None) is None


def test_coverage_fraction_bounds():
    assert coverage_fraction([-180, -90, 180, 90], NEW_HAVEN) == 1.0
    assert coverage_fraction(KENYA, NEW_HAVEN) == 0.0
