#!/usr/bin/env python3
"""
Sequential clustering based on X-sorted blocks
"""
import json
import sys
import numpy as np

def cluster_sequential(blocks, avg_width, y_tolerance, image_height=1920):
    """
    X 역순 정렬된 블록을 순차적으로 읽으며 클러스터링

    Args:
        blocks: X 역순 정렬된 블록 리스트 [{"text": str, "bbox": [x,y,w,h]}]
        avg_width: 블록 width의 75% percentile
        y_tolerance: Y 방향 인접 판정 threshold
        image_height: 이미지 높이 (dynamic_threshold 계산용)
    """
    if not blocks:
        return []

    dynamic_threshold = avg_width * 2 - 10
    clusters = []
    current_cluster = []

    for block in blocks:
        x, y, w, h = block["bbox"]

        if not current_cluster:
            # 첫 블록 - 새 클러스터 시작
            current_cluster.append(block)
            continue

        # 현재 클러스터의 모든 블록과 비교
        merged = False

        for cluster_block in current_cluster:
            cx, cy, cw, ch = cluster_block["bbox"]

            # X diff 계산
            x_diff = abs(x - cx)

            # Y gap 계산 (세로 방향 간격)
            y_gap = max(0, y - (cy + ch), cy - (y + h))

            # Stage 판단
            if x_diff <= 10:
                # Stage 1: 같은 세로줄 - Y gap 체크
                if y_gap <= y_tolerance:
                    current_cluster.append(block)
                    merged = True
                    break
            elif 10 < x_diff <= 55:
                # Stage 2: 무조건 분리
                continue
            elif 55 < x_diff <= dynamic_threshold:
                # Stage 3: Y 시작점 비교
                y_start_diff = abs(y - cy)
                if y_start_diff <= y_tolerance:
                    current_cluster.append(block)
                    merged = True
                    break
            else:
                # Stage 4: 완전히 다른 세로줄 - 분리
                continue

        if not merged:
            # 새 클러스터 시작
            if current_cluster:
                clusters.append(current_cluster)
            current_cluster = [block]

    # 마지막 클러스터 추가
    if current_cluster:
        clusters.append(current_cluster)

    return clusters


def format_clusters(clusters):
    """클러스터를 JSON 포맷으로 변환"""
    result = []

    for i, cluster in enumerate(clusters, start=1):
        # Bounding poly 계산
        all_x = []
        all_y = []
        for block in cluster:
            x, y, w, h = block["bbox"]
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

        # Lines (개별 블록을 라인으로)
        lines = []
        for block in cluster:
            lines.append({
                "text": block["text"],
                "bbox": block["bbox"]
            })

        result.append({
            "cluster_id": i,
            "orientation": "vertical",
            "lines": lines,
            "bounding_poly": bounding_poly
        })

    return result


def main():
    if len(sys.argv) < 3:
        print("Usage: python cluster_sequential.py <cv_sorted.json> <output.json>")
        sys.exit(1)

    input_path = sys.argv[1]
    output_path = sys.argv[2]

    # Load sorted blocks
    with open(input_path, 'r', encoding='utf-8') as f:
        blocks = json.load(f)

    # Calculate parameters
    widths = [b["bbox"][2] for b in blocks]
    avg_width = np.percentile(widths, 75)
    y_tolerance = 1920 * 0.03  # 3% of image height

    print(f"Loaded {len(blocks)} blocks")
    print(f"Parameters: avg_width={avg_width:.1f}px, y_tolerance={y_tolerance:.1f}px")

    # Cluster
    clusters = cluster_sequential(blocks, avg_width, y_tolerance)
    print(f"Created {len(clusters)} clusters")

    # Format
    result = format_clusters(clusters)

    # Save
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"✅ Saved to {output_path}")


if __name__ == "__main__":
    main()
