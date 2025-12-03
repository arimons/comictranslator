#!/usr/bin/env python3
"""
CV Clustering Script (V4 - Region Growing Algorithm)
참조: ref/clustering_logic_documentation.md
"""

import json
import sys
import re
import numpy as np
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Set


def load_cv_json(cv_path: str) -> List[Dict]:
    """CV JSON 로드"""
    with open(cv_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def convert_bbox_format(box_2d: List[int]) -> Tuple[int, int, int, int]:
    """
    CV 형식 변환: [ymin, xmin, ymax, xmax] → (x, y, width, height)
    """
    ymin, xmin, ymax, xmax = box_2d
    return (xmin, ymin, xmax - xmin, ymax - ymin)


def create_text_blocks(cv_data: List[Dict]) -> List[Dict]:
    """
    CV 데이터를 text block으로 변환

    Returns:
        [{"text": str, "bbox": [x, y, w, h]}, ...]
    """
    blocks = []
    for item in cv_data:
        x, y, w, h = convert_bbox_format(item["box_2d"])
        blocks.append({
            "text": item["label"],
            "bbox": [x, y, w, h]
        })

    print(f"  Total blocks: {len(blocks)}")
    return blocks


def calculate_image_size(blocks: List[Dict]) -> Tuple[int, int]:
    """이미지 크기 추정 (모든 bbox를 포함하는 최대 범위)"""
    if not blocks:
        return (1200, 2000)  # 기본값

    max_x = max(b["bbox"][0] + b["bbox"][2] for b in blocks)
    max_y = max(b["bbox"][1] + b["bbox"][3] for b in blocks)

    return (max_x, max_y)


def is_horizontal_text(block: Dict) -> bool:
    """텍스트가 가로쓰기인지 판단"""
    w, h = block["bbox"][2], block["bbox"][3]
    text_len = len(block["text"])
    aspect_ratio = h / w if w > 0 else 999
    return text_len >= 2 and aspect_ratio < 1


def filter_furigana_global(blocks: List[Dict]) -> List[Dict]:
    """
    전역 후리가나 필터링 (merge 전에 수행)

    세로쓰기 후리가나 조건:
    1. 오른쪽(X가 큰)에 다른 bbox가 있고
    2. 그 bbox의 width가 2배 이상이고
    3. Y 위치가 유사하면 (overlap 또는 gap < 20px)
    → 현재 bbox는 후리가나

    가로쓰기는 후리가나 필터링 제외 (후리가나가 위에 있어서 다른 로직 필요)
    """
    if not blocks:
        return blocks

    to_remove = set()

    for i, block in enumerate(blocks):
        if i in to_remove:
            continue

        # 가로쓰기는 후리가나 필터링 제외
        if is_horizontal_text(block):
            continue

        curr_x, curr_y, curr_w, curr_h = block["bbox"]
        curr_x2 = curr_x + curr_w
        curr_y2 = curr_y + curr_h

        # 가로쓰기 주변 체크 (X gap < 10px인 가로쓰기 bbox가 있으면 보호)
        near_horizontal = False
        for other in blocks:
            if is_horizontal_text(other):
                other_x, other_y, other_w, other_h = other["bbox"]
                x_gap = min(abs(curr_x - (other_x + other_w)), abs(other_x - curr_x2))
                y_gap = max(0, curr_y - (other_y + other_h), other_y - curr_y2)
                if x_gap < 10 and y_gap < 20:
                    near_horizontal = True
                    break

        if near_horizontal:
            continue

        # 오른쪽에 있는 bbox들 찾기 (X > curr_x) - 세로쓰기 전용
        for j, other in enumerate(blocks):
            if i == j or j in to_remove:
                continue

            # 가로쓰기는 비교 대상에서 제외
            if is_horizontal_text(other):
                continue

            other_x, other_y, other_w, other_h = other["bbox"]
            other_y2 = other_y + other_h

            # 오른쪽에 있는지 체크
            if other_x > curr_x:
                # Y overlap 체크
                y_overlap = not (curr_y2 < other_y or other_y2 < curr_y)
                y_gap = min(abs(curr_y - other_y2), abs(other_y - curr_y2))

                if y_overlap or y_gap < 20:
                    # Width 비교: 2배 이상 차이나면 후리가나
                    if curr_w <= other_w * 0.5:
                        to_remove.add(i)
                        print(f"    Filtered furigana: '{block['text']}' (w={curr_w} vs kanji={other_w})")
                        break

    # 필터링
    filtered = [block for i, block in enumerate(blocks) if i not in to_remove]
    return filtered


def _is_adjacent(
    block1: Dict,
    block2: Dict,
    x_tolerance: int,
    y_tolerance: int,
    avg_width: float
) -> bool:
    """
    인접성 판단 (세로쓰기 + 가로쓰기)

    가로쓰기 감지: 두 bbox 모두 aspect ratio < 1이고 Y가 비슷하면
    → X gap (끝→시작) 체크

    세로쓰기: 기존 4단계 로직
    """
    x1, y1, w1, h1 = block1["bbox"]
    x2, y2, w2, h2 = block2["bbox"]

    # Y 관련 계산
    y1_end = y1 + h1
    y2_end = y2 + h2
    y_overlap = not (y1_end < y2 or y2_end < y1)
    y_gap = max(0, y2 - y1_end, y1 - y2_end)

    # 가로쓰기 감지: 하나라도 가로쓰기(aspect < 1)이고 Y가 유사하면 가로쓰기로 처리
    aspect1 = h1 / w1 if w1 > 0 else 999
    aspect2 = h2 / w2 if w2 > 0 else 999
    likely_horizontal = ((aspect1 < 1 or aspect2 < 1) and y_gap < 10)

    if likely_horizontal:
        # 가로쓰기: X gap (끝→시작) 체크
        # block1이 왼쪽, block2가 오른쪽인 경우
        if x1 < x2:
            x_gap = x2 - (x1 + w1)
        else:
            x_gap = x1 - (x2 + w2)

        # X gap이 작으면 같은 문장 (단, gap이 음수면 overlap이므로 별도 처리)
        if x_gap < 0:
            # Overlap: 세로쓰기로 처리 (가로쓰기는 overlap 안함)
            pass
        elif x_gap < 50:
            return True

    # 세로쓰기 로직
    x_diff = abs(x1 - x2)
    dynamic_threshold = avg_width * 2 - 10

    # Stage 1: 거의 같은 세로줄
    if x_diff <= 10:
        if y_gap <= y_tolerance:
            return True
        else:
            return False

    # Stage 2: 인접 세로줄 (Y 조건 체크)
    elif 10 < x_diff <= 30:
        if y_overlap or y_gap <= y_tolerance * 0.5:
            return True
        else:
            return False

    # Stage 3: 가로 배치 (한 글자 + 후리가나 등)
    elif 30 < x_diff <= dynamic_threshold:
        y_top_diff = abs(y1 - y2)
        if y_top_diff <= y_tolerance:
            return True
        else:
            return False

    # Stage 4: 완전히 다른 세로줄
    else:
        return False


def cluster_by_region_growing(
    blocks: List[Dict],
    image_size: Tuple[int, int],
    x_tolerance_ratio: float = 0.05,
    y_gap_ratio: float = 0.03
) -> List[List[Dict]]:
    """
    Region Growing 기반 BFS Clustering

    참조: clustering_logic_documentation.md - Algorithm 1
    """
    if not blocks:
        return []

    image_width, image_height = image_size
    x_tolerance = int(image_width * x_tolerance_ratio)
    y_tolerance = int(image_height * y_gap_ratio)

    print(f"  Image size: {image_width}x{image_height}")
    print(f"  x_tolerance: {x_tolerance}px, y_tolerance: {y_tolerance}px")

    # avg_width 계산 (75% percentile)
    widths = [b["bbox"][2] for b in blocks]
    avg_width = float(np.percentile(widths, 75))
    print(f"  avg_width (75%): {avg_width:.1f}px")
    print(f"  dynamic_threshold: {avg_width * 2 - 10:.1f}px")

    # Y 오름차순(위→아래), X 내림차순(우→좌) 정렬
    sorted_blocks = sorted(blocks, key=lambda b: (b["bbox"][1], -b["bbox"][0]))

    visited: Set[int] = set()
    clusters = []

    # 우상단부터 Seed로 선택
    for seed_idx, seed_block in enumerate(sorted_blocks):
        if seed_idx in visited:
            continue

        # 새 클러스터 시작 (BFS)
        queue = [seed_idx]
        current_cluster = [seed_block]
        visited.add(seed_idx)

        while queue:
            current_idx = queue.pop(0)
            current_block = sorted_blocks[current_idx]

            # 모든 미방문 블록 중 current_cluster의 어느 블록과라도 인접하면 병합
            for candidate_idx, candidate in enumerate(sorted_blocks):
                if candidate_idx in visited:
                    continue

                # current_cluster의 모든 블록과 비교
                for cluster_block in current_cluster:
                    if _is_adjacent(cluster_block, candidate, x_tolerance, y_tolerance, avg_width):
                        current_cluster.append(candidate)
                        queue.append(candidate_idx)
                        visited.add(candidate_idx)
                        break

        clusters.append(current_cluster)

    print(f"  Region growing: {len(clusters)} clusters")
    return clusters


def detect_orientation(line: List[Dict]) -> str:
    """
    Line의 방향 감지: 'vertical' 또는 'horizontal'

    우선순위:
    1. X position grouping + Y overlap: 2개 이상 X group이 있고 Y overlap < 50% → 세로쓰기
    2. 다글자 text aspect ratio: 2글자 이상 text만으로 판단 (1글자 제외)
    3. 모두 1글자인 경우: X gap vs Y gap 비교
    """
    if not line:
        return 'vertical'

    # 1. X position grouping 확인 (세로쓰기는 여러 column을 가지고 Y가 다름)
    x_positions = sorted(set(block["bbox"][0] for block in line))
    x_groups = []
    for x in x_positions:
        if not x_groups or x - x_groups[-1] > 10:  # 10px 이상 차이나면 다른 column
            x_groups.append(x)

    # 2개 이상의 X group이 있고, Y overlap이 작으면 세로쓰기
    if len(x_groups) >= 2:
        # Y overlap 확인 (가로쓰기는 Y가 비슷함)
        y_positions = [block["bbox"][1] for block in line]
        y_heights = [block["bbox"][3] for block in line]

        # 평균 Y overlap 계산
        total_overlap = 0
        for i, b1 in enumerate(line):
            for j, b2 in enumerate(line):
                if i >= j:
                    continue
                y1, h1 = b1["bbox"][1], b1["bbox"][3]
                y2, h2 = b2["bbox"][1], b2["bbox"][3]
                overlap = max(0, min(y1+h1, y2+h2) - max(y1, y2))
                total_overlap += overlap

        pair_count = len(line) * (len(line) - 1) / 2
        avg_overlap = total_overlap / pair_count if pair_count > 0 else 0
        avg_height = sum(y_heights) / len(y_heights) if y_heights else 0

        # Y overlap이 평균 높이의 50% 미만이면 세로쓰기 (Y가 다름)
        if avg_overlap < avg_height * 0.5:
            return 'vertical'

    # 2. 다글자 text만으로 aspect ratio 판단
    multi_char_blocks = [b for b in line if len(b["text"]) >= 2]

    if multi_char_blocks:
        # 다글자 text의 aspect ratio로 판단
        horizontal_count = 0
        for block in multi_char_blocks:
            w, h = block["bbox"][2], block["bbox"][3]
            aspect_ratio = h / w if w > 0 else 999

            if aspect_ratio < 1:
                horizontal_count += 1

        # 과반수가 가로쓰기 조건을 만족하면 horizontal
        return 'horizontal' if horizontal_count > len(multi_char_blocks) / 2 else 'vertical'

    # 3. 모두 1글자인 경우: Y gap vs X gap 비교
    if len(line) >= 2:
        # Y gap 계산 (세로쓰기)
        y_sorted = sorted(line, key=lambda b: b["bbox"][1])
        y_gaps = [y_sorted[i+1]["bbox"][1] - (y_sorted[i]["bbox"][1] + y_sorted[i]["bbox"][3])
                  for i in range(len(y_sorted)-1)]
        avg_y_gap = sum(y_gaps) / len(y_gaps) if y_gaps else 0

        # X gap 계산 (가로쓰기)
        x_sorted = sorted(line, key=lambda b: b["bbox"][0])
        x_gaps = [x_sorted[i+1]["bbox"][0] - (x_sorted[i]["bbox"][0] + x_sorted[i]["bbox"][2])
                  for i in range(len(x_sorted)-1)]
        avg_x_gap = sum(x_gaps) / len(x_gaps) if x_gaps else 0

        # X gap이 더 작으면 가로쓰기
        return 'horizontal' if avg_x_gap < avg_y_gap else 'vertical'

    # 기본값: 세로쓰기
    return 'vertical'


def sort_line(line: List[Dict], orientation: str) -> List[Dict]:
    """
    Line 내부 정렬

    세로쓰기: X 내림차순 (오른쪽→왼쪽), Y 오름차순
    가로쓰기: X 오름차순 (왼쪽→오른쪽)
    """
    if orientation == 'horizontal':
        return sorted(line, key=lambda b: b["bbox"][0])  # X 오름차순
    else:
        # X 내림차순, Y 오름차순
        return sorted(line, key=lambda b: (-b["bbox"][0], b["bbox"][1]))


def expand_cluster_bboxes(cluster: List[Dict], orientation: str, furigana_ratio: float = 0.4) -> List[Dict]:
    """
    Cluster 내 bbox 확장

    세로쓰기: X 그룹핑 → Y 통합 → X gap 메우기 → 후리가나 공간
    가로쓰기: 원본 bbox 유지 (확장 안함)
    """
    if len(cluster) <= 1:
        return cluster

    # 가로쓰기는 확장 안함 (이미 원본 bbox가 적절함)
    if orientation == 'horizontal':
        return cluster

    # 세로쓰기 확장 로직
    # X 그룹핑 (같은 세로줄)
    x_groups = {}
    x_tolerance = 10  # X 그룹핑 허용 오차

    for block in cluster:
        x, y, w, h = block["bbox"]
        x_center = x + w / 2

        # 기존 그룹 찾기
        matched = False
        for group_x in list(x_groups.keys()):
            if abs(x_center - group_x) <= x_tolerance:
                x_groups[group_x].append(block)
                matched = True
                break

        if not matched:
            x_groups[x_center] = [block]

    # X 내림차순 정렬 (오른쪽→왼쪽)
    x_groups_sorted = sorted(x_groups.items(), key=lambda item: -item[0])

    expanded = []

    for i, (group_x, group_blocks) in enumerate(x_groups_sorted):
        # 이 그룹의 Y 범위 (전체 cluster 기준)
        global_min_y = min(b["bbox"][1] for b in cluster)
        global_max_y = max(b["bbox"][1] + b["bbox"][3] for b in cluster)

        # 그룹 내 대표 bbox (첫 번째)
        repr_block = group_blocks[0]
        x, y, w, h = repr_block["bbox"]

        # Y 통합
        aligned_y = global_min_y
        aligned_h = global_max_y - global_min_y

        # X gap 메우기 및 width 계산
        if i == 0:
            # 가장 오른쪽: 후리가나 공간 확보
            expansion_w = int(w * furigana_ratio)
            aligned_w = w + expansion_w
        else:
            # 중간/왼쪽: 이전(오른쪽) 그룹의 X까지 확장
            prev_x = x_groups_sorted[i - 1][1][0]["bbox"][0]
            aligned_w = prev_x - x

        # 그룹의 모든 텍스트 합치기
        group_text = "".join([b["text"] for b in group_blocks])

        expanded.append({
            "text": group_text,
            "bbox": [x, aligned_y, aligned_w, aligned_h]
        })

    return expanded


def calculate_bounding_poly(line: List[Dict]) -> List[List[int]]:
    """
    Bounding polygon 계산 (단순 직사각형 4 포인트)
    """
    if not line:
        return [[0, 0], [0, 0], [0, 0], [0, 0]]

    # 전체 bounding box
    all_x = [b["bbox"][0] for b in line]
    all_y = [b["bbox"][1] for b in line]
    all_x2 = [b["bbox"][0] + b["bbox"][2] for b in line]
    all_y2 = [b["bbox"][1] + b["bbox"][3] for b in line]

    bound_x = min(all_x)
    bound_y = min(all_y)
    bound_x2 = max(all_x2)
    bound_y2 = max(all_y2)

    # 단순 사각형 반환
    return [
        [bound_x, bound_y],
        [bound_x2, bound_y],
        [bound_x2, bound_y2],
        [bound_x, bound_y2]
    ]


def create_clusters(lines: List[List[Dict]]) -> List[Dict]:
    """
    Lines를 Cluster로 변환
    """
    clusters = []

    for line in lines:
        if not line:
            continue

        # 방향 감지
        orientation = detect_orientation(line)

        # 정렬
        sorted_line = sort_line(line, orientation)

        # Bbox 확장
        expanded_line = expand_cluster_bboxes(sorted_line, orientation)

        # Bounding polygon
        bounding_poly = calculate_bounding_poly(expanded_line)

        # Combined text
        combined_text = "".join([b["text"] for b in expanded_line])

        clusters.append({
            "bboxes": expanded_line,
            "bounding_poly": bounding_poly,
            "text": combined_text,
            "orientation": orientation
        })

    return clusters


def sort_clusters_reading_order(clusters: List[Dict]) -> List[Dict]:
    """
    만화 읽기 순서로 cluster 정렬: 우→좌, 상→하
    """
    def get_sort_key(cluster):
        if not cluster["bboxes"]:
            return (0, 0)

        # 가장 오른쪽, 가장 위 bbox
        rightmost_x = max(b["bbox"][0] for b in cluster["bboxes"])
        topmost_y = min(b["bbox"][1] for b in cluster["bboxes"])

        return (-rightmost_x, topmost_y)

    return sorted(clusters, key=get_sort_key)


def process_cv_to_clusters(cv_path: str) -> List[Dict]:
    """
    메인 처리 함수: CV JSON → Clusters
    """
    print(f"Loading {cv_path}...")
    cv_data = load_cv_json(cv_path)

    # 1. Text blocks 생성
    blocks = create_text_blocks(cv_data)

    # 2. 이미지 크기 추정
    image_size = calculate_image_size(blocks)

    # 3. 후리가나 필터링 (merge 전에!)
    blocks = filter_furigana_global(blocks)
    print(f"  After furigana filter: {len(blocks)} blocks")

    # 4. Region Growing Clustering
    lines = cluster_by_region_growing(blocks, image_size)

    # 5. Cluster 생성
    clusters = create_clusters(lines)

    # 6. 읽기 순서 정렬
    sorted_clusters = sort_clusters_reading_order(clusters)

    # 7. Cluster ID 부여
    for i, cluster in enumerate(sorted_clusters, start=1):
        cluster["cluster_id"] = i
        # bboxes와 bounding_poly를 맨 앞으로
        cluster_reordered = {
            "cluster_id": cluster["cluster_id"],
            "bboxes": cluster["bboxes"],
            "bounding_poly": cluster["bounding_poly"],
            "text": cluster["text"],
            "orientation": cluster["orientation"]
        }
        sorted_clusters[i-1] = cluster_reordered

    return sorted_clusters


def format_json_output(clusters: List[Dict]) -> str:
    """
    JSON 포맷팅: bbox와 bounding_poly를 한 줄로 표시
    """
    json_str = json.dumps(clusters, ensure_ascii=False, indent=2)

    # bbox를 한 줄로: [x, y, w, h]
    json_str = re.sub(
        r'"bbox":\s*\[\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+)\s*\]',
        r'"bbox": [\1, \2, \3, \4]',
        json_str
    )

    # bounding_poly를 한 줄로: [[x1, y1], [x2, y2], ...]
    def compress_poly(match):
        content = match.group(1)
        # 모든 공백과 줄바꿈 제거
        compressed = re.sub(r'\s+', '', content)
        # 콤마 뒤에 공백 하나 추가 (가독성)
        compressed = re.sub(r',', ', ', compressed)
        return f'"bounding_poly": [{compressed}]'

    # Non-greedy matching for nested arrays
    json_str = re.sub(
        r'"bounding_poly":\s*\[(\s*\[.*?\]\s*(?:,\s*\[.*?\]\s*)*)\]',
        compress_poly,
        json_str,
        flags=re.DOTALL
    )

    return json_str


def main():
    if len(sys.argv) < 2:
        print("Usage: python cluster_cv_v4.py <cv.json> [output.json]")
        sys.exit(1)

    cv_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else cv_path.replace("_cv.json", "_cv_clusters_v4.json")

    # 처리
    clusters = process_cv_to_clusters(cv_path)

    # JSON 저장
    json_str = format_json_output(clusters)

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(json_str)

    print(f"✅ Saved {len(clusters)} clusters to {output_path}")


if __name__ == "__main__":
    main()
