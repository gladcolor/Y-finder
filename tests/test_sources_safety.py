"""Safety checks on model-chosen URLs and query strings (no network needed for these cases)."""
import pytest

from yfinder import sources


@pytest.mark.parametrize("url", ["https://127.0.0.1/rest/services/x/FeatureServer/0", "https://localhost/a",
                                 "http://example.com/rest/services/x/FeatureServer/0", "https://10.0.0.5/x",
                                 "https://169.254.169.254/latest/meta-data", "file:///etc/passwd"])
def test_safe_url_blocks_private_and_plain_http(url):
    assert not sources.safe_url(url)


def test_arcgis_layer_url_parsing():
    assert sources.arcgis_layer_url("https://a.b/arcgis/rest/services/X/FeatureServer") == "https://a.b/arcgis/rest/services/X/FeatureServer/0"
    assert sources.arcgis_layer_url("https://a.b/arcgis/rest/services/X/MapServer/8?f=json") == "https://a.b/arcgis/rest/services/X/MapServer/8"
    assert sources.arcgis_layer_url("https://a.b/some/page.html") is None


def test_arcgis_query_refuses_private_host():
    with pytest.raises(ValueError):
        sources.arcgis_query("https://127.0.0.1/arcgis/rest/services/X/FeatureServer/0", [-73, 41, -72.9, 41.1])


def test_arcgis_where_clause_whitelist():
    with pytest.raises(ValueError):
        sources.arcgis_query("https://services.arcgis.com/x/arcgis/rest/services/X/FeatureServer/0", None, where="1=1; DROP TABLE")


@pytest.mark.parametrize("tag", ['natural=water"];out;', "a b c", "", "x=<script>"])
def test_osm_tag_filter_rejects_injection(tag):
    with pytest.raises(ValueError):
        sources.osm_features([tag], [-72.99, 41.25, -72.9, 41.33])


def test_osm_refuses_huge_area():
    with pytest.raises(ValueError):
        sources.osm_features(["natural=water"], [-74, 40, -71, 43])
