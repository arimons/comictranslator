#!/usr/bin/env python3
"""
CV OCR Clustering v5 - 2-Pass Algorithm
- Pass 1: Block -> Line (Y 방향 확장, BFS, Stage 1 only)
- Furigana Filtering: Line level (60% width threshold)
- Pass 2: Line -> Cluster (4-stage rule, BFS)
- Bbox Expansion: Rightmost line width + 60%
"""
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple, Optional
import numpy as np


def load_cv_json(filepath: str) -> List[Dict]:
    """CV JSON 파일 로드"""
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)


def convert_bbox_format(box_2d: List[int]) -> Tuple[int, int, int, int]:
    """[ymin, xmin, ymax, xmax] → (x, y, width, height)"""
    ymin, xmin, ymax, xmax = box_2d
    return (xmin, ymin, xmax - xmin, ymax - ymin)


def create_text_blocks(cv_data: List[Dict]) -> List[Dict]:
    """CV 데이터를 text block으로 변환"""
    blocks = []
    for item in cv_data:
        x, y, w, h = convert_bbox_format(item["box_2d"])
        blocks.append({
            "text": item["label"],
            "bbox": [x, y, w, h]
        })
    return blocks


def calculate_image_size(blocks: List[Dict]) -> Tuple[int, int]:
    if not blocks:
        return (1200, 2000)
    max_x = max(b["bbox"][0] + b["bbox"][2] for b in blocks)
    max_y = max(b["bbox"][1] + b["bbox"][3] for b in blocks)
    return (max_x, max_y)


def is_horizontal_text(block: Dict) -> bool:
    """블록이 가로쓰기인지 확인"""
    x, y, w, h = block["bbox"]
    return w > h * 1.5


def calculate_bbox_union(bboxes: List[List[int]]) -> List[int]:
    """여러 bbox의 합집합 계산 [x, y, w, h]"""
    if not bboxes:
        return [0, 0, 0, 0]
    
    min_x = min(b[0] for b in bboxes)
    min_y = min(b[1] for b in bboxes)
    max_x = max(b[0] + b[2] for b in bboxes)
    max_y = max(b[1] + b[3] for b in bboxes)
    
    return [min_x, min_y, max_x - min_x, max_y - min_y]


# ==========================================
# Pass 1: Block -> Line
# ==========================================

def pass1_create_lines(blocks: List[Dict], avg_width: float, y_tolerance: float) -> List[Dict]:
    """
    Pass 1: Block -> Line (BFS 방식)
    같은 X 그룹에서 Y 축으로만 합침 (Stage 1 only)
    """
    if not blocks:
        return []

    # Sort by X descending (right to left for vertical Japanese)
    blocks_sorted = sorted(blocks, key=lambda b: -b["bbox"][0])

    visited = set()
    lines = []

    for seed_idx, seed_block in enumerate(blocks_sorted):
        if seed_idx in visited:
            continue

        # BFS
        queue = [seed_block]
        current_line_blocks = [seed_block]
        visited.add(seed_idx)

        while queue:
            curr = queue.pop(0)
            cx, cy, cw, ch = curr["bbox"]

            # Check all unvisited blocks
            for cand_idx, candidate in enumerate(blocks_sorted):
                if cand_idx in visited:
                    continue

                x, y, w, h = candidate["bbox"]
                x_diff = abs(x - cx)
                y_gap = max(0, y - (cy + ch), cy - (y + h))

                # Stage 1 ONLY: X diff ≤ 10px AND Y gap ≤ tolerance
                # 같은 X 그룹(세로줄)에서 Y 방향으로만 병합
                if x_diff <= 10 and y_gap <= y_tolerance:
                    current_line_blocks.append(candidate)
                    queue.append(candidate)
                    visited.add(cand_idx)

        # Create Line object
        lines.append(create_line_object(current_line_blocks))

    return lines


def create_line_object(blocks: List[Dict]) -> Dict:
    """블록 리스트로부터 Line 객체 생성"""
    # Calculate union bbox
    all_x = []
    all_y = []
    for block in blocks:
        x, y, w, h = block["bbox"]
        all_x.extend([x, x + w])
        all_y.extend([y, y + h])

    min_x, max_x = min(all_x), max(all_x)
    min_y, max_y = min(all_y), max(all_y)

    line_bbox = [min_x, min_y, max_x - min_x, max_y - min_y]

    # X 좌표로 그룹핑 (±5px 이내는 같은 그룹)
    x_groups = []
    blocks_by_x = sorted(blocks, key=lambda b: -b["bbox"][0])  # X 큰 순

    for block in blocks_by_x:
        x = block["bbox"][0]
        # 기존 그룹에 속하는지 확인
        added = False
        for group in x_groups:
            if abs(group['x'] - x) <= 5:  # ±5px 이내면 같은 그룹
                group['blocks'].append(block)
                added = True
                break
        if not added:
            x_groups.append({'x': x, 'blocks': [block]})

    # 각 그룹 내에서 Y 정렬 후 합치기
    blocks_sorted = []
    for group in x_groups:
        # Y 작은 순 (위→아래)
        group['blocks'].sort(key=lambda b: b["bbox"][1])
        blocks_sorted.extend(group['blocks'])

    text = "".join([b["text"] for b in blocks_sorted])

    # Determine orientation
    w, h = line_bbox[2], line_bbox[3]
    is_vertical = h > w * 1.2

    return {
        "bboxes": blocks_sorted,  # Keep original blocks for reference
        "bbox": line_bbox,
        "text": text,
        "orientation": "vertical" if is_vertical else "horizontal"
    }


# ==========================================
# Furigana Filtering
# ==========================================

def filter_furigana_lines(lines: List[Dict], avg_width: float) -> List[Dict]:
    """
    후리가나 필터링 (Line 레벨)

    조건:
    1. 바로 왼쪽 Line의 Y 범위와 겹침 (overlap) - 완전 포함이 아닌 겹침으로 완화
    2. Width가 왼쪽 Line의 60% 이하
    3. X gap 무시 (인접 여부만 확인)
    """
    if not lines:
        return lines

    # Sort by X descending (right to left)
    lines_sorted = sorted(lines, key=lambda l: -l["bbox"][0])

    filtered = []
    for i, line in enumerate(lines_sorted):
        lx, ly, lw, lh = line["bbox"]
        line_y_end = ly + lh

        # 바로 왼쪽 Line 찾기 (X 좌표가 더 작은 첫 번째)
        is_furigana = False
        for j in range(i + 1, len(lines_sorted)):
            left = lines_sorted[j]
            left_x, left_y, left_w, left_h = left["bbox"]
            left_y_end = left_y + left_h

            # Y 범위 겹침 체크: ±50px 허용 (한자 누락 대응)
            # 완전 겹침이 아니어도 세로 방향으로 가까우면 후리가나로 간주
            y_tolerance = 50
            y_overlap = not (line_y_end < left_y - y_tolerance or ly > left_y_end + y_tolerance)

            if y_overlap:
                # Width 체크 (60% 이하)
                if lw <= left_w * 0.60:
                    print(f"  ✂️ Furigana: '{line['text']}' (w={lw}, y={ly}~{line_y_end}) near '{left['text']}' (w={left_w}, y={left_y}~{left_y_end})")
                    is_furigana = True
                    break

            # 첫 번째 인접 Line만 체크 (X 좌표 차이가 크면 중단)
            if left_x < lx - 100:  # 충분히 멀어지면 중단
                break

        if not is_furigana:
            filtered.append(line)

    print(f"  Furigana filter: {len(lines)} → {len(filtered)} lines")
    return filtered


# ==========================================
# Pass 2: Line -> Cluster
# ==========================================

def pass2_create_clusters(lines: List[Dict], avg_width: float, y_tolerance: float) -> List[Dict]:
    """
    Pass 2: Line -> Cluster (BFS 방식)

    후리가나 필터링 후 남은 Line들을 4-stage rule로 Clustering:
    - Stage 1: X diff ≤ 10px → Y gap 체크 (특수문자 뒤면 1.5배 완화)
    - Stage 2: 10 < X diff ≤ 55px → Y 시작점 유사하면 병합
    - Stage 3: 55 < X diff ≤ dynamic_threshold → Y 시작점 유사하면 병합
    - Stage 4: X diff > dynamic_threshold → 분리
    """
    if not lines:
        return []

    dynamic_threshold = avg_width * 2.5  # Line 레벨에서는 더 넓게

    # Sort lines by X descending (right to left)
    lines_sorted = sorted(lines, key=lambda l: -l["bbox"][0])

    visited = set()
    clusters = []

    for seed_idx, seed_line in enumerate(lines_sorted):
        if seed_idx in visited:
            continue

        # BFS
        queue = [seed_line]
        current_cluster = [seed_line]
        visited.add(seed_idx)

        while queue:
            curr = queue.pop(0)
            cx, cy, cw, ch = curr["bbox"]
            curr_text = curr.get("text", "").strip()

            # Check all unvisited lines
            for cand_idx, candidate in enumerate(lines_sorted):
                if cand_idx in visited:
                    continue

                lx, ly, lw, lh = candidate["bbox"]
                x_diff = abs(lx - cx)
                y_gap = max(0, ly - (cy + ch), cy - (ly + lh))
                y_start_diff = abs(ly - cy)

                # Stage 1: X diff ≤ 10px (같은 세로줄)
                if x_diff <= 10:
                    # ✨ NEW: 현재 line이 특수문자로 끝나면 y_tolerance 1.5배 완화
                    # CV bbox 추정 오류(… 등) 보정
                    effective_tolerance = y_tolerance
                    if curr_text and curr_text[-1] in ('…', '!', '?', '！', '？', '。', 'っ', 'た', 'だ'):
                        effective_tolerance = y_tolerance * 1.5
                        if y_gap > y_tolerance and y_gap <= effective_tolerance:
                            print(f"  🔄 Stage1 relaxed: '{curr_text}' (y_gap={y_gap:.1f}px > {y_tolerance:.1f}px) + '{candidate['text']}' [special_char_suffix]")

                    if y_gap <= effective_tolerance:
                        current_cluster.append(candidate)
                        queue.append(candidate)
                        visited.add(cand_idx)
                # Stage 2: 10 < X diff ≤ 55px
                elif 10 < x_diff <= 55:
                    if y_start_diff <= y_tolerance:
                        current_cluster.append(candidate)
                        queue.append(candidate)
                        visited.add(cand_idx)
                # Stage 3: 55 < X diff ≤ dynamic_threshold
                elif 55 < x_diff <= dynamic_threshold:
                    if y_start_diff <= y_tolerance:
                        current_cluster.append(candidate)
                        queue.append(candidate)
                        visited.add(cand_idx)
                # Stage 4: X diff > dynamic_threshold → separate

        clusters.append(current_cluster)

    return clusters


def merge_adjacent_lines(lines: List[Dict], y_tolerance: float) -> List[Dict]:
    """
    같은 X 좌표의 인접한 라인들을 병합

    Y-gap이 y_tolerance 이내인 같은 X 좌표 라인들을 합침
    텍스트: 위에서 아래 순서로 연결
    bbox: 통합 (min_x, min_y, width, max_height)
    """
    if not lines:
        return []

    merged = []
    current_group = [lines[0]]

    for i in range(1, len(lines)):
        curr_line = lines[i]
        prev_line = current_group[-1]

        cx, cy, cw, ch = curr_line["bbox"]
        px, py, pw, ph = prev_line["bbox"]

        x_diff = abs(cx - px)
        y_gap = max(0, cy - (py + ph), py - (cy + ch))

        # ✨ 같은 X 좌표 (±10px) + Y gap이 tolerance 이내면 병합 대상
        if x_diff <= 10 and y_gap <= y_tolerance:
            current_group.append(curr_line)
        else:
            # 현재 그룹 병합 후 result에 추가
            if len(current_group) > 1:
                merged.append(merge_line_group(current_group))
                print(f"    🔗 Merged {len(current_group)} lines: {' + '.join([l['text'] for l in current_group])} → '{merged[-1]['text']}'")
            else:
                merged.append(current_group[0])
            current_group = [curr_line]

    # 마지막 그룹 처리
    if current_group:
        if len(current_group) > 1:
            merged.append(merge_line_group(current_group))
            print(f"    🔗 Merged {len(current_group)} lines: {' + '.join([l['text'] for l in current_group])} → '{merged[-1]['text']}'")
        else:
            merged.append(current_group[0])

    return merged


def merge_line_group(group: List[Dict]) -> Dict:
    """라인 그룹을 하나의 라인으로 병합"""
    if len(group) == 1:
        return group[0]

    # Y 좌표순 정렬 (위에서 아래)
    group_sorted = sorted(group, key=lambda l: l["bbox"][1])

    # 텍스트 병합
    merged_text = ''.join([l["text"] for l in group_sorted])

    # bbox 통합: [x, y, w, h]
    all_x = []
    all_y = []
    for line in group_sorted:
        x, y, w, h = line["bbox"]
        all_x.extend([x, x + w])
        all_y.extend([y, y + h])

    min_x, max_x = min(all_x), max(all_x)
    min_y, max_y = min(all_y), max(all_y)

    return {
        "text": merged_text,
        "bbox": [min_x, min_y, max_x - min_x, max_y - min_y]
    }


def format_clusters(clusters: List[List[Dict]], avg_width: float) -> List[Dict]:
    """클러스터를 JSON 포맷으로 변환"""
    result = []
    y_tolerance = avg_width * 1.5  # Line 병합 기준

    for i, cluster in enumerate(clusters, start=1):
        # Line 정렬: X 큰 순 (우→좌 읽기)
        cluster_sorted = sorted(cluster, key=lambda l: -l["bbox"][0])

        # ✨ NEW: 같은 X 좌표의 인접한 라인들을 병합
        merged_lines = merge_adjacent_lines(cluster_sorted, y_tolerance)

        # Bounding poly 계산
        all_x = []
        all_y = []

        for j, line in enumerate(merged_lines):
            x, y, w, h = line["bbox"]

            # ✅ 마지막 글자가 특수문자인 경우 height 5px 확장
            line_text = line.get("text", "").strip()
            if line_text and line_text.endswith(('た', 'だ', '?', '.', '！', '。', 'つ')):
                h += 5

            # 맨 오른쪽 Line (첫 번째)만 width 확장
            if j == 0:
                expansion = int(w * 0.6)
                all_x.extend([x, x + w + expansion])  # 오른쪽으로 확장
            else:
                all_x.extend([x, x + w])

            all_y.extend([y, y + h])

        min_x, max_x = min(all_x), max(all_x)
        min_y, max_y = min(all_y), max(all_y)

        # Y축 패딩
        y_padding = int(avg_width * 0.15)
        adj_min_y = max(0, min_y - y_padding)
        adj_max_y = max_y + y_padding

        # bounding_rect
        bounding_rect = [
            min_x,
            adj_min_y,
            max_x - min_x,
            adj_max_y - adj_min_y
        ]

        # Lines (병합된 라인들)
        lines_output = []
        for line in merged_lines:
            lines_output.append({
                "text": line["text"],
                "bbox": line["bbox"]
            })

        result.append({
            "cluster_id": i,
            "orientation": "vertical",
            "lines": lines_output,
            "bounding_rect": bounding_rect
        })

    return result


def format_json_output(clusters: List[Dict]) -> str:
    """
    JSON 포맷팅: bbox와 bounding_rect를 한 줄로 표시
    """
    # Custom serialization to ensure bbox arrays are on single lines
    def format_cluster(cluster: Dict, indent: int = 2) -> str:
        lines = []
        ind = " " * indent
        lines.append("{")
        
        # cluster_id
        lines.append(f'{ind}  "cluster_id": {cluster["cluster_id"]},')
        
        # orientation
        lines.append(f'{ind}  "orientation": "{cluster["orientation"]}",')
        
        # lines array
        lines.append(f'{ind}  "lines": [')
        for i, line in enumerate(cluster["lines"]):
            comma = "," if i < len(cluster["lines"]) - 1 else ""
            bbox = line["bbox"]
            bbox_str = f'[{bbox[0]}, {bbox[1]}, {bbox[2]}, {bbox[3]}]'
            # Escape any quotes in text
            text = line["text"].replace('"', '\\"')
            lines.append(f'{ind}    {{"text": "{text}", "bbox": {bbox_str}}}{comma}')
        lines.append(f'{ind}  ],')
        
        # bounding_rect: [x, y, w, h]
        rect = cluster["bounding_rect"]
        rect_str = f'[{rect[0]}, {rect[1]}, {rect[2]}, {rect[3]}]'
        lines.append(f'{ind}  "bounding_rect": {rect_str}')
        
        lines.append(f'{ind}}}')
        return "\n".join(lines)
    
    # Format all clusters
    result_lines = ["["]
    for i, cluster in enumerate(clusters):
        comma = "," if i < len(clusters) - 1 else ""
        result_lines.append(format_cluster(cluster, indent=2) + comma)
    result_lines.append("]")
    
    return "\n".join(result_lines)


def sort_clusters_reading_order(clusters: List[Dict]) -> List[Dict]:
    def get_pos(c):
        rect = c['bounding_rect']
        x, y, w, h = rect
        return (-x, y)  # Right-to-left (vertical), top-to-bottom
    return sorted(clusters, key=get_pos)


def process_cv_clusters(cv_path: str, output_path: str, image_width: int = 1350, image_height: int = 1920):
    print(f"Loading {cv_path}...")
    cv_data = load_cv_json(cv_path)

    # 1. Blocks
    blocks = create_text_blocks(cv_data)

    # Calculate parameters
    widths = [b['bbox'][2] for b in blocks]
    avg_width = np.percentile(widths, 75) if widths else 50.0
    y_tolerance = image_height * 0.015  # 1.5% of image height (약 29px for 1920)

    print(f"  Loaded {len(blocks)} blocks")
    print(f"  Parameters: avg_width={avg_width:.1f}px, y_tolerance={y_tolerance:.1f}px")

    # 2. Pass 1: Block -> Line
    lines = pass1_create_lines(blocks, avg_width, y_tolerance)
    print(f"  Pass 1: Created {len(lines)} lines")

    # 3. Furigana Filter
    lines = filter_furigana_lines(lines, avg_width)

    # 4. Pass 2: Line -> Cluster
    clusters = pass2_create_clusters(lines, avg_width, y_tolerance)
    print(f"  Pass 2: Created {len(clusters)} clusters")

    # 5. Format clusters
    result = format_clusters(clusters, avg_width)

    # 6. Sort by reading order (right to left, top to bottom)
    result = sort_clusters_reading_order(result)

    # 7. Reassign cluster IDs after sorting
    for i, cluster in enumerate(result, start=1):
        cluster["cluster_id"] = i

    return result


def main():
    if len(sys.argv) != 3:
        print("Usage: python cluster_cv_v5.py <cv_json> <output_json>")
        sys.exit(1)
        
    cv_path = sys.argv[1]
    output_path = sys.argv[2]
    
    if not Path(cv_path).exists():
        print(f"Error: {cv_path} not found")
        sys.exit(1)
        
    clusters = process_cv_clusters(cv_path, output_path)
    
    # Use custom formatter to ensure single-line bbox arrays
    json_output = format_json_output(clusters)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(json_output)
        
    print(f"✅ Saved {len(clusters)} clusters to {output_path}")


if __name__ == "__main__":
    main()
