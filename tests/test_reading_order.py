"""칸 여백 기반 R 읽기 순서."""
import numpy as np

from pipeline.reading_order import _bands, panel_cells, reading_sequence


def page():
    # 흰 페이지에 테두리 있는 칸 3개: 위 한 칸, 아래 오른쪽·왼쪽 두 칸(여백 4px)
    gray = np.full((600, 400), 255, np.uint8)
    for x0, y0, x1, y1 in ((20, 20, 380, 280), (204, 290, 380, 580), (20, 290, 200, 580)):
        gray[y0:y1, x0:x1] = 120
    return gray


def test_cells_top_then_right_then_left():
    assert panel_cells(page()) == [[20, 20, 360, 260], [204, 290, 176, 290], [20, 290, 180, 290]]


def test_sequence_follows_panels_then_right_to_left_inside_a_panel():
    items = {
        "top_left": [40, 40, 60, 60], "top_right": [300, 50, 60, 60], "top_low": [200, 120, 60, 50],
        "br": [300, 320, 50, 50], "bl": [40, 300, 50, 50], "br_low": [220, 480, 50, 50],
    }
    ordered = reading_sequence([{"name": k, "rect": v} for k, v in items.items()], page())
    # 같은 칸 안에서는 y와 상관없이 x가 큰 쪽이 먼저다
    assert [entry["name"] for entry in ordered] == ["top_right", "top_low", "top_left", "br", "br_low", "bl"]


def test_bands_split_merged_panels_by_y_center_gap():
    # 위 칸 말풍선(y 중심 100·130)과 아래 칸 말풍선(400·430) 사이 간격이 커서 두 띠로 나뉜다
    items = [{"name": n, "rect": [x, y - 20, 40, 40]} for n, x, y in (("a", 50, 100), ("b", 300, 130), ("c", 350, 400), ("d", 60, 430))]
    assert [[entry["name"] for entry in band] for band in _bands(items, 1000)] == [["a", "b"], ["c", "d"]]
    # 간격이 페이지 높이의 20%보다 작으면 같은 칸으로 보고 나누지 않는다
    assert len(_bands(items, 2000)) == 1


def test_upper_tier_comes_first_even_when_art_breaks_every_gutter():
    # 위 단 두 칸(오른쪽·왼쪽)과 아래 단 두 칸. 아래 왼쪽 그림이 위 칸 경계까지 이어져 가로·세로 여백이 모두 끊긴다
    gray = np.full((800, 600), 255, np.uint8)
    for x0, y0, x1, y1 in ((310, 20, 580, 380), (20, 20, 300, 380), (240, 400, 580, 780)):
        gray[y0:y1, x0:x1] = 120
    gray[300:780, 20:240] = 90
    assert len(panel_cells(gray)) == 1
    items = {"top_right": [480, 60, 60, 120], "top_left": [120, 80, 60, 120],
             "bottom_right": [500, 450, 60, 120], "bottom_left": [60, 520, 60, 120]}
    ordered = reading_sequence([{"name": k, "rect": v} for k, v in items.items()], gray)
    # 예전에는 페이지 전체가 한 칸이라 x 순서(bottom_right가 1번)였다
    assert [entry["name"] for entry in ordered] == ["top_right", "top_left", "bottom_right", "bottom_left"]


def test_tall_side_panel_is_not_cut_into_tiers():
    # 오른쪽 칸과 그 옆의 세로로 긴 왼쪽 칸. 왼쪽 칸 대사의 y 간격이 커도 오른쪽 칸과 번갈아 읽지 않는다
    gray = np.full((900, 600), 255, np.uint8)
    for x0, y0, x1, y1 in ((310, 20, 580, 700), (20, 60, 300, 700), (20, 720, 580, 880)):
        gray[y0:y1, x0:x1] = 120
    items = {"left_top": [150, 80, 60, 120], "right_a": [450, 300, 60, 120], "right_b": [380, 480, 60, 120],
             "left_mid": [100, 330, 60, 120], "left_low": [60, 500, 60, 120], "bottom": [300, 760, 60, 100]}
    ordered = reading_sequence([{"name": k, "rect": v} for k, v in items.items()], gray)
    assert [entry["name"] for entry in ordered] == ["right_a", "right_b", "left_top", "left_mid", "left_low", "bottom"]


def test_split_pieces_of_same_parent_stay_together_and_wide_spread_reads_right_page_first():
    gray = np.full((400, 800), 255, np.uint8)
    pieces = [{"name": "a2", "rect": [500, 50, 100, 100], "sub": 2e-5}, {"name": "a1", "rect": [500, 50, 100, 100], "sub": 1e-5},
              {"name": "left_page", "rect": [100, 20, 50, 50]}]
    assert [entry["name"] for entry in reading_sequence(pieces, gray, wide_spread=True)] == ["a1", "a2", "left_page"]
