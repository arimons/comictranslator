#!/usr/bin/env python3
"""
2-Pass Sequential Clustering
Pass 1: Block -> Line (Y 방향 확장, 4-stage rule)
Pass 2: Line -> Cluster (X 방향, Y 시작점 유사성)
"""
import json
import sys
import numpy as np


def pass1_create_lines(blocks, avg_width, y_tolerance):
    """
    Pass 1: Block -> Line (BFS 방식)
    같은 X 그룹에서 Y 축으로만 합침 (Stage 1 only)
    """
    if not blocks:
        return []

    # Sort by X descending (right to left)
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


def create_line_object(blocks):
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

    # Sort blocks: X 큰 순 (우→좌), 같으면 Y 작은 순 (상→하)
    blocks_sorted = sorted(blocks, key=lambda b: (-b["bbox"][0], b["bbox"][1]))
    text = "".join([b["text"] for b in blocks_sorted])

    return {
        "text": text,
        "bbox": line_bbox,
        "blocks": blocks_sorted  # Keep original blocks for reference
    }


def filter_furigana_lines(lines, avg_width):
    """
    후리가나 필터링 (Line 레벨)

    조건:
    1. 바로 왼쪽 Line의 Y 범위에 완전히 포함
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

            # Y 범위 포함 체크: 현재 Line이 왼쪽 Line의 Y 범위에 완전히 포함되는가?
            # (ly >= left_y) and (line_y_end <= left_y_end)
            if ly >= left_y and line_y_end <= left_y_end:
                # Width 체크 (60%로 증가)
                if lw <= left_w * 0.60:
                    print(f"  ✂️ Furigana: '{line['text']}' (w={lw}, y={ly}~{line_y_end}) inside '{left['text']}' (w={left_w}, y={left_y}~{left_y_end})")
                    is_furigana = True
                    break

            # 첫 번째 인접 Line만 체크 (X 좌표 차이가 크면 중단)
            if left_x < lx - 100:  # 충분히 멀어지면 중단
                break

        if not is_furigana:
            filtered.append(line)

    print(f"  Furigana filter: {len(lines)} → {len(filtered)} lines")
    return filtered


def pass2_create_clusters(lines, avg_width, y_tolerance):
    """
    Pass 2: Line -> Cluster (BFS 방식)

    후리가나 필터링 후 남은 Line들을 4-stage rule로 Clustering:
    - Stage 1: X diff ≤ 10px → Y gap 체크
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
                    if y_gap <= y_tolerance:
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


def format_clusters(clusters):
    """클러스터를 JSON 포맷으로 변환"""
    result = []

    for i, cluster in enumerate(clusters, start=1):
        # Line 정렬: X 큰 순 (우→좌 읽기)
        cluster_sorted = sorted(cluster, key=lambda l: -l["bbox"][0])

        # Bounding poly 계산
        # 맨 오른쪽 Line (cluster_sorted[0])의 width를 60% 확장
        all_x = []
        all_y = []

        for j, line in enumerate(cluster_sorted):
            x, y, w, h = line["bbox"]

            # 맨 오른쪽 Line (첫 번째)만 width 확장
            if j == 0:
                expansion = int(w * 0.6)
                all_x.extend([x, x + w + expansion])  # 오른쪽으로 확장
            else:
                all_x.extend([x, x + w])

            all_y.extend([y, y + h])

        min_x, max_x = min(all_x), max(all_x)
        min_y, max_y = min(all_y), max(all_y)

        bounding_poly = [
            [min_x, min_y],
            [max_x, min_y],
            [max_x, max_y],
            [min_x, max_y]
        ]

        # Lines (text와 bbox) - 정렬된 순서로 (원본 bbox 유지)
        lines_output = []
        for line in cluster_sorted:
            lines_output.append({
                "text": line["text"],
                "bbox": line["bbox"]
            })

        result.append({
            "cluster_id": i,
            "orientation": "vertical",
            "lines": lines_output,
            "bounding_poly": bounding_poly
        })

    return result


def format_json_output(clusters):
    """JSON 포맷팅: bbox와 bounding_poly를 한 줄로"""
    lines = ["["]

    for i, cluster in enumerate(clusters):
        comma = "," if i < len(clusters) - 1 else ""
        lines.append("  {")
        lines.append(f'    "cluster_id": {cluster["cluster_id"]},')
        lines.append(f'    "orientation": "{cluster["orientation"]}",')
        lines.append('    "lines": [')

        for j, line in enumerate(cluster["lines"]):
            line_comma = "," if j < len(cluster["lines"]) - 1 else ""
            bbox = line["bbox"]
            bbox_str = f'[{bbox[0]}, {bbox[1]}, {bbox[2]}, {bbox[3]}]'
            text = line["text"].replace('"', '\\"')
            lines.append(f'      {{"text": "{text}", "bbox": {bbox_str}}}{line_comma}')

        lines.append('    ],')

        poly = cluster["bounding_poly"]
        poly_str = f'[[{poly[0][0]}, {poly[0][1]}], [{poly[1][0]}, {poly[1][1]}], [{poly[2][0]}, {poly[2][1]}], [{poly[3][0]}, {poly[3][1]}]]'
        lines.append(f'    "bounding_poly": {poly_str}')
        lines.append(f'  }}{comma}')

    lines.append("]")
    return "\n".join(lines)


def main():
    if len(sys.argv) < 3:
        print("Usage: python cluster_2pass.py <cv_sorted.json> <output.json>")
        sys.exit(1)

    input_path = sys.argv[1]
    output_path = sys.argv[2]

    # Load sorted blocks
    with open(input_path, 'r', encoding='utf-8') as f:
        blocks = json.load(f)

    # Calculate parameters
    widths = [b["bbox"][2] for b in blocks]
    avg_width = np.percentile(widths, 75)
    y_tolerance = 1920 * 0.015  # 1.5% of image height (약 29px)

    print(f"Loaded {len(blocks)} blocks")
    print(f"Parameters: avg_width={avg_width:.1f}px, y_tolerance={y_tolerance:.1f}px")

    # Pass 1: Block -> Line
    lines = pass1_create_lines(blocks, avg_width, y_tolerance)
    print(f"Pass 1: Created {len(lines)} lines")

    # Furigana filter
    lines = filter_furigana_lines(lines, avg_width)

    # Pass 2: Line -> Cluster
    clusters = pass2_create_clusters(lines, avg_width, y_tolerance)
    print(f"Pass 2: Created {len(clusters)} clusters")

    # Format
    result = format_clusters(clusters)

    # Save with custom formatting
    json_output = format_json_output(result)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(json_output)

    print(f"✅ Saved to {output_path}")


if __name__ == "__main__":
    main()
