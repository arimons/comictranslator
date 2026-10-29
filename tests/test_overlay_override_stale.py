from pipeline.render_text_overlay import overlay_override_for_cluster


def cluster(anchor, source_region_id):
    return {"matched_cluster": [anchor], "source_region_id": source_region_id}


def test_stale_cluster_keeps_own_region_number():
    # 옛 R2(앵커2)가 제외돼 앵커3이 현재 R2가 된 상태. 옛 R2의 빈 텍스트가 옛 R3 번역을 지우면 안 된다.
    flood = {1: {"region_id": 1}, 3: {"region_id": 2}}
    overrides = {"2": {"text_override": ""}}
    assert overlay_override_for_cluster(cluster(2, 2), flood, overrides) == {"text_override": ""}
    assert overlay_override_for_cluster(cluster(3, 3), flood, overrides) == {}


def test_cluster_without_region_number_still_uses_anchor():
    flood = {3: {"region_id": 2}}
    overrides = {"2": {"text_override": "x"}}
    assert overlay_override_for_cluster({"matched_cluster": [3]}, flood, overrides) == {"text_override": "x"}
