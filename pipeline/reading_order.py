"""만화 칸(패널) 읽기 순서. 칸 사이 흰 여백으로 페이지를 재귀적으로 나눈다(XY-cut).

가로 흰 줄로 먼저 위→아래 띠를 나누고, 띠 안은 세로 흰 줄로 오른쪽→왼쪽 칸을 나눈다. 칸 안에서 다시 같은 방식으로 나눈다.
흰 여백이 끊긴 칸(그림이 칸 밖으로 넘친 경우)은 나뉘지 않고 한 칸으로 남는다.
"""
from __future__ import annotations

import numpy as np

# 여백으로 볼 밝기와, 여백 줄에서 밝은 픽셀이 차지해야 하는 비율.
# 배경이 거의 흰 칸도 테두리 선은 있으므로, 여백 줄은 거의 완전히 밝아야 한다.
WHITE_LEVEL = 200
WHITE_FILL = 0.995
MAX_DEPTH = 6


def _white_runs(line_white: np.ndarray, min_gap: int) -> list[tuple[int, int]]:
    """양 끝(여백)을 제외한 내부의 흰 줄 구간 [start, end)."""
    runs = []
    start = None
    for index, value in enumerate(line_white):
        if value and start is None:
            start = index
        elif not value and start is not None:
            runs.append((start, index))
            start = None
    if start is not None:
        runs.append((start, len(line_white)))
    return [(a, b) for a, b in runs if b - a >= min_gap and a > 0 and b < len(line_white)]


def _trim(white: np.ndarray, x0: int, y0: int, x1: int, y1: int) -> tuple[int, int, int, int] | None:
    """상자 가장자리의 흰 여백을 걷어낸 내용 범위."""
    sub = white[y0:y1, x0:x1]
    rows = np.flatnonzero(sub.mean(axis=1) < WHITE_FILL)
    cols = np.flatnonzero(sub.mean(axis=0) < WHITE_FILL)
    if not rows.size or not cols.size:
        return None
    return x0 + int(cols[0]), y0 + int(rows[0]), x0 + int(cols[-1]) + 1, y0 + int(rows[-1]) + 1


def panel_cells(gray: np.ndarray) -> list[list[int]]:
    """읽기 순서대로 정렬된 칸 상자 [x, y, w, h] 목록. 여백을 못 찾으면 페이지 전체 한 칸이다."""
    height, width = gray.shape[:2]
    white = gray >= WHITE_LEVEL
    # 칸 사이 여백은 4px 남짓으로 좁을 수 있다. 대신 여백 줄은 칸 높이(폭) 전체가 거의 완전히 밝아야 한다
    min_gap = max(3, round(min(width, height) * 0.003))
    cells: list[list[int]] = []

    def split(x0: int, y0: int, x1: int, y1: int, depth: int) -> None:
        box = _trim(white, x0, y0, x1, y1)
        if box is None:
            return
        x0, y0, x1, y1 = box
        if depth < MAX_DEPTH:
            sub = white[y0:y1, x0:x1]
            # 가로 여백이 먼저다: 위→아래 띠
            rows = _white_runs(sub.mean(axis=1) >= WHITE_FILL, min_gap)
            if rows:
                edges = [y0] + [y0 + (a + b) // 2 for a, b in rows] + [y1]
                for top, bottom in zip(edges, edges[1:]):
                    split(x0, top, x1, bottom, depth + 1)
                return
            # 세로 여백: 오른쪽→왼쪽 칸
            cols = _white_runs(sub.mean(axis=0) >= WHITE_FILL, min_gap)
            if cols:
                edges = [x0] + [x0 + (a + b) // 2 for a, b in cols] + [x1]
                pieces = list(zip(edges, edges[1:]))
                for left, right in reversed(pieces):
                    split(left, y0, right, y1, depth + 1)
                return
        cells.append([x0, y0, x1 - x0, y1 - y0])

    split(0, 0, width, height, 0)
    return cells or [[0, 0, width, height]]


def cell_index(cells: list[list[int]], cx: float, cy: float) -> int:
    """점이 들어 있는 칸 번호. 여백 위에 있으면 가장 가까운 칸."""
    best, best_distance = 0, float("inf")
    for index, (x, y, w, h) in enumerate(cells):
        dx = max(x - cx, 0.0, cx - (x + w))
        dy = max(y - cy, 0.0, cy - (y + h))
        distance = dx * dx + dy * dy
        if distance == 0:
            return index
        if distance < best_distance:
            best, best_distance = index, distance
    return best


def _slanted_gutter(white: np.ndarray, cell: list[int], y_lo: float, y_hi: float) -> bool:
    """칸 폭 전체를 가로지르는 비스듬한(또는 수평) 흰 여백선이 y_lo-y_hi 사이에 있는지 본다.

    XY-cut은 수평·수직 여백만 찾으므로, 칸 경계가 비스듬해 두 칸이 합쳐진 경우를 여기서 확인한다.
    선의 양 끝 높이는 각각 y_lo-y_hi 범위에서 고르고, 기울기는 칸 폭의 25% 이내로 제한한다.
    """
    x, _, w, _ = cell
    top, bottom = int(max(0, y_lo)), int(min(white.shape[0] - 1, y_hi))
    if bottom - top < 3 or w < 10:
        return False
    xs = np.arange(x, x + w)
    t = (xs - x) / max(1, w - 1)
    rights = np.arange(top, bottom + 1, 2)
    for left in range(top, bottom + 1, 2):
        rights_ok = rights[np.abs(rights - left) <= 0.25 * w]
        if not rights_ok.size:
            continue
        ys = np.rint(left + np.outer(rights_ok - left, t)).astype(int)
        if (white[ys, xs[None, :]].mean(axis=1) >= 0.98).any():
            return True
    return False


def _bands(items: list[dict], page_height: float) -> list[list[dict]]:
    """한 칸 안을 y 중심이 몰린 구간(띠)으로 나눈다. 여백이 비스듬해 여러 칸이 한 칸으로 합쳐졌을 때를 대비한다.

    y 중심 사이 간격이 칸 안 평소 간격(중앙값)의 2.5배 이상이고 페이지 높이의 20% 이상이면 띠 경계로 본다.
    같은 부모에서 나뉜 조각처럼 중심이 같은 항목은 중앙값을 0으로 끌어내리지 않게 한 번만 센다.
    같은 칸의 말풍선은 대개 이 간격보다 촘촘해 한 띠로 남고, 띠 안은 x가 큰 쪽(오른쪽)이 무조건 먼저다.
    """
    ordered = sorted(items, key=lambda entry: entry["rect"][1] + entry["rect"][3] / 2)
    centers = [entry["rect"][1] + entry["rect"][3] / 2 for entry in ordered]
    gaps = [b - a for a, b in zip(centers, centers[1:])]
    floor = page_height * 0.2
    # 칸 안의 평소 간격은 큰 간격(칸 경계 후보)을 빼고 추정한다. 간격이 몇 개뿐일 때 큰 간격이 중앙값을 끌어올리지 않게 한다.
    typical = [gap for gap in gaps if 1 < gap < floor]
    threshold = max(float(np.median(typical)) * 2.5, floor) if typical else floor
    bands = [[ordered[0]]]
    for gap, entry in zip(gaps, ordered[1:]):
        if gap > threshold:
            bands.append([])
        bands[-1].append(entry)
    return bands


def _panel_bands(items: list[dict], white: np.ndarray, cell: list[int]) -> list[list[dict]]:
    """y 간격으로 나눈 띠 중, 사이에 실제 비스듬한 여백선이 있는 경계만 남긴다. 같은 칸이면 나누지 않는다."""
    bands = _bands(items, white.shape[0])
    merged = [bands[0]]
    for band in bands[1:]:
        upper = max(entry["rect"][1] + entry["rect"][3] / 2 for entry in merged[-1])
        lower = min(entry["rect"][1] + entry["rect"][3] / 2 for entry in band)
        if _slanted_gutter(white, cell, upper, lower):
            merged.append(band)
        else:
            merged[-1] = merged[-1] + band
    return merged


def reading_sequence(items: list[dict], gray: np.ndarray, wide_spread: bool = False) -> list[dict]:
    """읽기 순서대로 정렬한 items. 각 item은 rect([x,y,w,h])와 선택 키 sub(같은 부모 조각끼리의 순서)를 가진다.

    순서: (펼침면이면 오른쪽 쪽 먼저) → 칸(위→아래, 오른쪽→왼쪽) → 칸 안의 띠(위→아래) → 띠 안은 x가 큰 쪽부터.
    """
    if not items:
        return []
    width = gray.shape[1]
    cells = panel_cells(gray)
    white = gray >= WHITE_LEVEL
    groups: dict[tuple[int, int], list[dict]] = {}
    for item in items:
        x, y, w, h = item["rect"]
        cx, cy = x + w / 2, y + h / 2
        page = 1 if (wide_spread and cx < width / 2) else 0
        groups.setdefault((page, cell_index(cells, cx, cy)), []).append(item)
    ordered: list[dict] = []
    for key in sorted(groups):
        for band in _panel_bands(groups[key], white, cells[key[1]]):
            ordered.extend(sorted(band, key=lambda entry: (-(entry["rect"][0] + entry["rect"][2] / 2), entry.get("sub", 0.0))))
    return ordered
