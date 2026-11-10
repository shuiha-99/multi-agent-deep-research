import pytest

from app.engine.runner import validate_live_report


@pytest.mark.parametrize("citation", ["[source_unknown]", "`source_unknown`", "source_unknown"])
def test_foreign_citation_is_rejected_in_every_display_format(citation):
    with pytest.raises(ValueError, match="证据编号"):
        validate_live_report(citation, [{"id": "source_owned"}], [])


def test_owned_inline_citations_normalize_and_foreign_artifacts_fail():
    sources = [{"id": "source_owned"}]
    assert validate_live_report("事实 `source_owned`。", sources, []) == "事实 [source_owned]。"
    assert validate_live_report("source_type 为来源类别。", sources, [])
    with pytest.raises(ValueError, match="产物链接"):
        validate_live_report("![图](/api/artifacts/artifact_foreign)", sources, [{"id": "artifact_owned"}])
    with pytest.raises(ValueError, match="可核验"):
        validate_live_report("未检索但给出结论", [], [])
