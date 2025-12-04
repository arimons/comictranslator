#!/usr/bin/env python3
"""
CV OCR Clustering v5 Refactored
- Stage 1: Block -> Line (Strict vertical/horizontal merging)
- Stage 2: Furigana Filtering (Line level)
- Stage 3: Line -> Cluster (Paragraph grouping)
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
# Stage 1: Block -> Line
# ==========================================

def _is_same_line(block1: Dict, block2: Dict, avg_width: float) -> bool:
    """
    두 블록이 '같은 라인'에 속하는지 판단 (Strict)
    """
    x1, y1, w1, h1 = block1["bbox"]
    x2, y2, w2, h2 = block2["bbox"]
    
    # 1. Vertical Line Check
    # X 좌표가 거의 같아야 함
    x_diff = abs(x1 - x2)
    x_center_diff = abs((x1 + w1/2) - (x2 + w2/2))
    
    # Y Gap
    y1_end = y1 + h1
    y2_end = y2 + h2
    y_gap = max(0, y2 - y1_end, y1 - y2_end)
    
    # Vertical 조건:
    # - X 정렬이 잘 되어 있어야 함 (width의 50% 이내 오차)
    # - Y Gap이 너무 크지 않아야 함 (글자 크기의 1.5배 이내?)
    char_size = max(w1, w2, h1, h2) # 대략적인 글자 크기
    
    if x_center_diff <= avg_width * 0.5 and y_gap <= char_size * 1.5:
        return True
        
    # 2. Horizontal Line Check
    # Y 좌표가 거의 같아야 함
    y_center_diff = abs((y1 + h1/2) - (y2 + h2/2))
    x_gap = max(0, x2 - (x1 + w1), x1 - (x2 + w2))
    
    if y_center_diff <= h1 * 0.5 and x_gap <= char_size * 1.5:
        # 가로쓰기 텍스트인 경우에만 허용
        if is_horizontal_text(block1) or is_horizontal_text(block2):
            return True
            
    return False


def create_lines(blocks: List[Dict]) -> List[Dict]:
    """
    Blocks -> Lines (Region Growing)
    Returns: List of Line Dicts
    """
    if not blocks:
        return []

    # avg_width 계산
    widths = [b["bbox"][2] for b in blocks]
    avg_width = float(np.percentile(widths, 75))
    
    # 정렬 (Y 우선)
    sorted_blocks = sorted(blocks, key=lambda b: (b["bbox"][1], b["bbox"][0]))
    
    visited = set()
    lines = []
    
    for seed_idx, seed_block in enumerate(sorted_blocks):
        if seed_idx in visited:
            continue
            
        queue = [seed_block]
        current_line_blocks = [seed_block]
        visited.add(seed_idx)
        
        while queue:
            curr = queue.pop(0)
            
            for cand_idx, candidate in enumerate(sorted_blocks):
                if cand_idx in visited:
                    continue
                
                if _is_same_line(curr, candidate, avg_width):
                    current_line_blocks.append(candidate)
                    queue.append(candidate)
                    visited.add(cand_idx)
        
        # Line 생성
        line_bbox = calculate_bbox_union([b["bbox"] for b in current_line_blocks])
        text = "".join([b["text"] for b in current_line_blocks]) # 순서 정렬 필요?
        
        # 블록 내부 정렬 (세로: Y, 가로: X)
        w, h = line_bbox[2], line_bbox[3]
        is_vertical = h > w * 1.2
        
        if is_vertical:
            current_line_blocks.sort(key=lambda b: b["bbox"][1])
        else:
            current_line_blocks.sort(key=lambda b: b["bbox"][0])
            
        text = "".join([b["text"] for b in current_line_blocks])
        
        lines.append({
            "bboxes": current_line_blocks, # Original blocks
            "bbox": line_bbox,             # Line BBox
            "text": text,
            "orientation": "vertical" if is_vertical else "horizontal"
        })
        
    return lines


# ==========================================
# Stage 2: Furigana Filtering
# ==========================================

def filter_furigana_lines(lines: List[Dict]) -> List[Dict]:
    """
    Line 레벨 후리가나 필터링
    """
    if not lines:
        return []
        
    print(f"\n  === Furigana Filtering ===")
    
    # Vertical lines only for filtering
    # But we need to keep horizontal lines too
    
    # Sort by X (Right to Left) for processing
    # We need to preserve original indices or work on a copy
    
    # 인덱스 부여
    for i, line in enumerate(lines):
        line['_id'] = i
        
    vertical_lines = [l for l in lines if l['orientation'] == 'vertical']
    vertical_lines.sort(key=lambda l: l['bbox'][0], reverse=True)
    
    to_remove_ids = set()
    
    for i, curr in enumerate(vertical_lines):
        if curr['_id'] in to_remove_ids:
            continue
            
        curr_x, curr_y, curr_w, curr_h = curr['bbox']
        
        # Check left neighbors
        for j in range(i + 1, min(i + 10, len(vertical_lines))):
            next_l = vertical_lines[j]
            if next_l['_id'] in to_remove_ids:
                continue
                
            next_x, next_y, next_w, next_h = next_l['bbox']
            
            # Gap check
            # curr is Right, next is Left
            # Gap = Right_X - (Left_X + Left_W)
            # Wait, curr is Right (larger X), next is Left (smaller X)
            # Gap = curr_x - (next_x + next_w)
            
            gap = curr_x - (next_x + next_w)
            
            # Y Overlap check
            overlap_start = max(curr_y, next_y)
            overlap_end = min(curr_y + curr_h, next_y + next_h)
            overlap_h = overlap_end - overlap_start
            
            if overlap_h >= 10 and gap <= 20:
                # Width check
                if curr_w <= next_w * 0.55:
                    print(f"  ✂️ Furigana detected: '{curr['text']}' (w={curr_w}) next to '{next_l['text']}' (w={next_w}), gap={gap}")
                    to_remove_ids.add(curr['_id'])
                else:
                    # Not furigana, but close. Stop checking further left?
                    # Maybe not, could be multiple columns
                    pass
            elif gap > 200: # Too far, stop checking this pair
                pass
                
    filtered_lines = [l for l in lines if l['_id'] not in to_remove_ids]
    print(f"  Removed {len(to_remove_ids)} furigana lines. Total lines: {len(lines)} -> {len(filtered_lines)}")
    
    return filtered_lines


# ==========================================
# Stage 3: Line -> Cluster
# ==========================================

def _is_same_cluster(line1: Dict, line2: Dict) -> bool:
    """
    두 라인이 같은 클러스터(문단)인지 판단
    """
    # Orientation이 다르면 병합 X
    if line1['orientation'] != line2['orientation']:
        return False
        
    l1_bbox = line1['bbox']
    l2_bbox = line2['bbox']
    
    if line1['orientation'] == 'vertical':
        # Vertical Lines: Group if adjacent horizontally
        # X Gap should be small-ish (paragraph spacing)
        # Y Overlap should be significant
        
        x1, y1, w1, h1 = l1_bbox
        x2, y2, w2, h2 = l2_bbox
        
        # Gap calculation (assuming sorted or using min/max)
        gap_x = max(0, x2 - (x1 + w1), x1 - (x2 + w2))
        
        # Y Overlap
        overlap_start = max(y1, y2)
        overlap_end = min(y1 + h1, y2 + h2)
        overlap_h = overlap_end - overlap_start
        
        # Thresholds
        # Gap: 1.5 * width? or fixed?
        # Let's use a dynamic threshold based on width
        avg_w = (w1 + w2) / 2
        
        # Reduced threshold from 2.0 to 1.5 to prevent merging separate bubbles
        if gap_x <= avg_w * 1.5 and overlap_h > min(h1, h2) * 0.3:
            return True
            
    else:
        # Horizontal Lines: Group if adjacent vertically
        x1, y1, w1, h1 = l1_bbox
        x2, y2, w2, h2 = l2_bbox
        
        gap_y = max(0, y2 - (y1 + h1), y1 - (y2 + h2))
        
        # X Overlap
        overlap_start = max(x1, x2)
        overlap_end = min(x1 + w1, x2 + w2)
        overlap_w = overlap_end - overlap_start
        
        avg_h = (h1 + h2) / 2
        
        if gap_y <= avg_h * 1.5 and overlap_w > min(w1, w2) * 0.3:
            return True
            
    return False


def create_clusters_from_lines(lines: List[Dict]) -> List[Dict]:
    """
    Lines -> Clusters
    """
    if not lines:
        return []
        
    # Sort for processing
    # Vertical: Right to Left
    # Horizontal: Top to Bottom
    # Mixed... let's just use a generic sort
    sorted_lines = sorted(lines, key=lambda l: (l['bbox'][1], l['bbox'][0]))
    
    visited = set()
    clusters = []
    
    for seed_idx, seed_line in enumerate(sorted_lines):
        if seed_idx in visited:
            continue
            
        queue = [seed_line]
        current_cluster_lines = [seed_line]
        visited.add(seed_idx)
        
        while queue:
            curr = queue.pop(0)
            
            for cand_idx, candidate in enumerate(sorted_lines):
                if cand_idx in visited:
                    continue
                
                if _is_same_cluster(curr, candidate):
                    current_cluster_lines.append(candidate)
                    queue.append(candidate)
                    visited.add(cand_idx)
                    
        # Cluster 생성
        # Calculate Cluster BBox
        cluster_bbox = calculate_bbox_union([l['bbox'] for l in current_cluster_lines])
        
        # Text
        # Sort lines based on reading order
        # Vertical: Right to Left
        # Horizontal: Top to Bottom
        orientation = current_cluster_lines[0]['orientation'] # Assume majority or seed
        
        if orientation == 'vertical':
            current_cluster_lines.sort(key=lambda l: l['bbox'][0], reverse=True)
        else:
            current_cluster_lines.sort(key=lambda l: l['bbox'][1])
            
        full_text = "\n".join([l['text'] for l in current_cluster_lines])
        
        # Apply Width Expansion (Vertical only, ONLY to the rightmost line)
        # User request: "맨 오른쪽 bbox 는 width 50% 확장하던 로직을 폐기하고 맨 오른쪽 인식된 CV가 후리가나일 경우 이 width 를 합치는 로직이 있었어. 그냥 다시 살아남은 최종 bbox 는 width 55% 확장으로 로직을 바꾸자."
        # Clarification: "Cluster width의 55%가 아니고 최우측 lines width 의 55%야."
        # Clarification 2: "expanded_bbox 개념을 잘못 이해하고 있어. 위 케이스는 ... 이것이 맨 오른쪽 bbox 이기 때문에 얘의 width 만 55% 확장해서 29 -> 45 로 확장."
        
        if orientation == 'vertical':
            # Find rightmost line (max x)
            # lines are already sorted by X descending (Right to Left) for vertical
            # So lines[0] is the rightmost line
            rightmost_line = current_cluster_lines[0]
            
            # Calculate expansion for the rightmost line
            blocks = rightmost_line.get('bboxes', [])
            if blocks:
                # Use MAX width of blocks (Kanji width) for robust expansion
                max_block_w = max(b['bbox'][2] for b in blocks)
                expansion = int(max_block_w * 0.55) # Reverted to 55% as per user request
                
                # Expand line bbox
                l_x, l_y, l_w, l_h = rightmost_line['bbox']
                # Update the bbox directly? User said "expanded_bbox 개념을 잘못 이해하고 있어"
                # But in the example: "bbox": [1184,179,29,187] ... "expanded_bbox": [1135,173,46,125] (Wait, example image shows expanded_bbox key)
                # User said: "지금 결과 보니 명확하네 expanded_bbox 개념을 잘못 이해하고 있어."
                # "얘의 width 만 55% 확장해서 29 -> 45 로 확장."
                # So I should update the bbox of the rightmost line? Or keep original bbox and update cluster poly?
                # User's desired JSON structure doesn't show 'expanded_bbox' in 'lines'.
                # It shows 'lines' -> 'text', 'bbox'.
                # And then 'bounding_poly' for the cluster.
                # So the expansion should affect the CLUSTER's bounding_poly, but maybe not the line's bbox in the JSON?
                # "Cluster를 외접하는 bounding poly 만 그렸는데 개별 line의 경계도 그려줘."
                # If I expand the line bbox, the visualization will show the expanded box for the line.
                # Let's update the line's bbox in memory to calculate the correct cluster poly, 
                # but maybe keep the original bbox for 'lines' output if user wants "original line boundary".
                # User said: "CV box -> region grow ... = CV line"
                # "맨 오른쪽 bbox 는 width 50% 확장... 최종 bbox 는 width 55% 확장"
                # I will store the expanded version in a temporary variable to calculate the cluster polygon,
                # but I will NOT modify the 'bbox' field of the line in the output, unless requested.
                # Wait, if I don't modify the line bbox, how does the user know it's expanded?
                # The user complained about "expansion": 17 field.
                # And said "expanded_bbox 개념을 잘못 이해하고 있어".
                # Let's assume the expansion is ONLY for the Cluster's bounding_poly coverage.
                # So:
                # 1. Identify rightmost line.
                # 2. Calculate expansion.
                # 3. Adjust Cluster BBox (Poly) to include this expansion.
                # 4. Do NOT change line['bbox'] in output.
                
                # But wait, "얘의 width 만 55% 확장해서 29 -> 45 로 확장."
                # This implies the line itself is considered wider.
                # Let's update the cluster polygon construction logic.
                
                pass # Logic handled below in bounding_poly calculation
            else:
                expansion = 0
                max_block_w = 0
        else:
            expansion = 0
            rightmost_line = None

        # Calculate Cluster BBox and Poly
        # We need to account for the expansion of the rightmost line in the cluster poly
        
        # Collect all line bboxes
        all_bboxes = []
        for line in current_cluster_lines:
            lx, ly, lw, lh = line['bbox']
            if line is rightmost_line:
                # Expand this one for the purpose of Cluster Poly
                # Expansion is to the RIGHT?
                # "세로쓰기는 우->좌로 읽으므로, 후리가나는 보통 우측에 있음."
                # Image coords: X increases to Right.
                # So we need to increase Max X.
                # bbox: [x, y, w, h]. x is left. x+w is right.
                # So we add to w.
                all_bboxes.append([lx, ly, lw + expansion, lh])
                print(f"    Expanding Cluster Poly for Line '{line['text'][:5]}': +{expansion}px (55% of {max_block_w}px)")
            else:
                all_bboxes.append([lx, ly, lw, lh])
        
        cluster_bbox = calculate_bbox_union(all_bboxes)
        cx, cy, cw, ch = cluster_bbox
        
        bounding_poly = [
            [cx, cy],
            [cx + cw, cy],
            [cx + cw, cy + ch],
            [cx, cy + ch]
        ]
        
        # Clean up lines for output
        output_lines = []
        for line in current_cluster_lines:
            # Remove internal keys
            clean_line = {
                "text": line['text'],
                "bbox": line['bbox']
            }
            output_lines.append(clean_line)

        # Reorder keys as requested: Cluster_id -> orientation -> lines -> bounding_poly
        # Note: cluster_id is added later
        clusters.append({
            "orientation": orientation,
            "lines": output_lines,
            "bounding_poly": bounding_poly,
            # Internal use for sorting/ID assignment
            "bbox": cluster_bbox, 
            "text": full_text 
        })
        
    return clusters


def format_json_output(clusters: List[Dict]) -> str:
    """
    JSON 포맷팅: bbox와 bounding_poly를 한 줄로 표시
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
        
        # bounding_poly
        poly = cluster["bounding_poly"]
        poly_str = f'[[{poly[0][0]}, {poly[0][1]}], [{poly[1][0]}, {poly[1][1]}], [{poly[2][0]}, {poly[2][1]}], [{poly[3][0]}, {poly[3][1]}]]'
        lines.append(f'{ind}  "bounding_poly": {poly_str}')
        
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
        poly = c['bounding_poly']
        return (-min(p[0] for p in poly), min(p[1] for p in poly))
    return sorted(clusters, key=get_pos)


def process_cv_clusters(cv_path: str, output_path: str):
    print(f"Loading {cv_path}...")
    cv_data = load_cv_json(cv_path)
    
    # 1. Blocks
    blocks = create_text_blocks(cv_data)
    
    # 2. Lines
    lines = create_lines(blocks)
    print(f"  Created {len(lines)} lines")
    
    # 3. Furigana Filter
    lines = filter_furigana_lines(lines)
    
    # 4. Clusters
    clusters = create_clusters_from_lines(lines)
    print(f"  Created {len(clusters)} clusters")
    
    # 5. Sort
    clusters = sort_clusters_reading_order(clusters)
    
    # 6. ID & Reorder
    for i, cluster in enumerate(clusters, start=1):
        cluster["cluster_id"] = i
        
        # Reorder keys
        # User requested:
        # "cluster_id": 1,
        # "orientation" : "vertical"
        # lines
        # bounding_poly
        
        ordered_cluster = {
            "cluster_id": cluster["cluster_id"],
            "orientation": cluster["orientation"],
            "lines": cluster["lines"],
            "bounding_poly": cluster["bounding_poly"]
        }
        clusters[i-1] = ordered_cluster
        
    return clusters


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
