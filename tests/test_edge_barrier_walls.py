import numpy as np

from pipeline.dialogue_extractor import _include_text_pixels_in_edge_mask
from pipeline.edge_barrier_flood_rescue import _draw_virtual_walls


def box_walls(x, y, w, h):
    pts = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    return [{"box_id": "b", "line": [list(pts[i]), list(pts[(i + 1) % 4])]} for i in range(4)]


def test_box_walls_are_drawn_outside_so_the_inside_stays_free():
    edges = np.zeros((120, 120), dtype=np.uint8)
    _draw_virtual_walls(edges, box_walls(30, 30, 60, 60), (0, 0), 9)
    # 박스 안쪽은(경계의 안티에일리어싱 1px 제외) 벽에 덮이지 않고, 바깥쪽은 벽이 막는다
    assert edges[32, 60] == 0 and edges[60, 32] == 0 and edges[88, 60] == 0 and edges[60, 88] == 0
    assert edges[27, 60] > 0 and edges[60, 27] > 0 and edges[93, 60] > 0 and edges[60, 93] > 0


def test_loose_walls_stay_centered():
    edges = np.zeros((60, 60), dtype=np.uint8)
    _draw_virtual_walls(edges, [{"line": [[10, 30], [50, 30]]}], (0, 0), 9)
    assert edges[28, 30] > 0 and edges[32, 30] > 0


def test_only_dark_text_pixels_are_added_not_the_whole_text_rect():
    image = np.full((100, 100, 3), 255, dtype=np.uint8)
    image[40:44, 40:44] = 0      # 글자 픽셀
    image[10:14, 80:84] = 0      # 사각형 밖 검은 선
    mask = np.zeros((100, 100), dtype=np.uint8)
    groups = [{"fallback_rect": [30, 30, 30, 30]}]
    result, rect = _include_text_pixels_in_edge_mask(mask, [0, 0, 0, 0], image, groups, [20, 20, 70, 70])
    assert result[41, 41] == 255            # 글자는 지운다
    assert result[33, 33] == 0              # 글자 사각형 안의 흰 바탕을 통째로 칠하지 않는다
    assert result[11, 81] == 0              # 사각형 밖 검은 선은 건드리지 않는다


def test_dark_pixels_in_text_rect_corners_outside_any_text_line_are_not_added():
    image = np.full((100, 100, 3), 255, dtype=np.uint8)
    image[30:40, 40:42] = 0      # 글자 줄 안의 글자 픽셀
    image[88:92, 28:32] = 0      # TG 사각형 모서리의 방사선 가시
    mask = np.zeros((100, 100), dtype=np.uint8)
    group = {"orientation": "vertical", "fallback_rect": [25, 25, 30, 70], "lines": [{"bbox": [38, 28, 8, 30]}]}
    result, _rect = _include_text_pixels_in_edge_mask(mask, [0, 0, 0, 0], image, [group], [20, 20, 70, 78])
    assert result[35, 40] == 255
    assert result[90, 30] == 0


def test_blob_connected_to_outside_spike_is_only_added_inside_the_text_line():
    image = np.full((100, 100, 3), 255, dtype=np.uint8)
    image[30:40, 40:42] = 0      # 글자 줄 안에 통째로 들어가는 글자(후리가나 포함)
    image[50:52, 60:90] = 0      # 줄 영역 밖으로 길게 이어지는 가시
    mask = np.zeros((100, 100), dtype=np.uint8)
    group = {"orientation": "vertical", "lines": [{"bbox": [38, 28, 8, 30]}, {"bbox": [58, 48, 4, 6]}]}
    result, _rect = _include_text_pixels_in_edge_mask(mask, [0, 0, 0, 0], image, [group], [20, 20, 75, 70], ruby_pad=2)
    assert result[35, 40] == 255
    assert result[51, 85] == 0   # 줄 영역 밖 가시는 지우지 않는다


def test_confined_edge_box_never_floods_outside_the_box_even_on_an_open_page():
    from pipeline.edge_barrier_flood_rescue import run_edge_barrier_rescue_auto

    image = np.full((260, 260, 3), 255, dtype=np.uint8)   # 윤곽선 없는 열린 흰 페이지(채우기가 끝없이 새는 상황)
    image[110:150, 120:126] = 0                            # 글자
    walls = box_walls(90, 80, 80, 100)
    group = {"text_group_id": 1, "fallback_rect": [118, 108, 12, 44], "tight_rect": [118, 108, 12, 44]}
    result = run_edge_barrier_rescue_auto(image, [group], [90, 80, 80, 100], virtual_walls=walls)
    mask = result["mask"] > 0
    assert result["success"]
    assert not mask[:80].any() and not mask[181:].any() and not mask[:, :90].any() and not mask[:, 171:].any()
    assert mask[130, 123]                                   # 박스 안 글자는 지운다


def test_seed_is_moved_off_the_wall_and_stays_inside_the_box():
    from pipeline.edge_barrier_flood_rescue import _block_outside_rect, _free_seed_inside_rect

    edges = np.zeros((100, 100), dtype=np.uint8)
    edges[30:36, 20:80] = 255                              # 시작점이 놓인 벽
    gray = np.full((100, 100), 255, dtype=np.uint8)
    edges = _block_outside_rect(edges, [20, 30, 60, 50])
    assert edges[10, 50] == 255                            # 박스 밖은 장벽
    seed = _free_seed_inside_rect((50, 32), edges, gray, [20, 30, 60, 50])
    assert seed is not None and edges[seed[1], seed[0]] == 0 and 20 <= seed[0] < 80 and 30 <= seed[1] < 80


def test_mask_polygon_follows_mask_shape_not_rect():
    import numpy as np
    from pipeline.dialogue_extractor import _mask_polygon

    mask = np.zeros((100, 100), np.uint8)
    mask[10:60, 10:60] = 255
    mask[10:30, 40:60] = 0  # 오른쪽 위 모서리를 깎아 사각형이 아닌 모양
    polygon = _mask_polygon(mask)
    assert polygon is not None and len(polygon) >= 5
    assert _mask_polygon(np.zeros((10, 10), np.uint8)) is None


def test_manual_split_on_polygon_only_region_stays_inside_polygon():
    import numpy as np
    import cv2
    from pipeline.dialogue_extractor import _split_region_variants

    shape = (200, 200)
    polygon = cv2.ellipse2Poly((100, 100), (60, 80), 0, 0, 360, 10).tolist()
    groups = {
        1: {"text_group_id": 1, "tight_rect": [80, 40, 40, 30], "bounding_rect": [80, 40, 40, 30]},
        2: {"text_group_id": 2, "tight_rect": [80, 130, 40, 30], "bounding_rect": [80, 130, 40, 30]},
    }
    region = {"is_valid": True, "anchor_ids": [1, 2], "bubble_rect": [40, 20, 120, 160], "polygon": polygon, "method": "flood"}
    out = _split_region_variants(region, groups, [{"line": [[20, 100], [180, 100]]}], image_shape=shape)
    assert len(out) == 2
    ellipse = np.zeros(shape, np.uint8)
    cv2.fillPoly(ellipse, [np.array(polygon, np.int32)], 255)
    for child in out:
        assert not np.any((child["mask"] > 0) & (ellipse == 0))
        assert child["polygon"] != polygon
