#!/usr/bin/env python3
"""
BBox 유틸리티 함수
IoU 계산, bbox 병합, 매칭 등
"""

from typing import List, Dict, Tuple, Optional


def calculate_iou(box1: List[int], box2: List[int]) -> float:
    """
    두 bbox의 IoU (Intersection over Union) 계산

    Args:
        box1: [ymin, xmin, ymax, xmax]
        box2: [ymin, xmin, ymax, xmax]

    Returns:
        float: IoU 값 (0.0 ~ 1.0)
    """
    y1_min, x1_min, y1_max, x1_max = box1
    y2_min, x2_min, y2_max, x2_max = box2

    # 교집합 영역 계산
    inter_xmin = max(x1_min, x2_min)
    inter_ymin = max(y1_min, y2_min)
    inter_xmax = min(x1_max, x2_max)
    inter_ymax = min(y1_max, y2_max)

    # 교집합이 없는 경우
    if inter_xmin >= inter_xmax or inter_ymin >= inter_ymax:
        return 0.0

    # 교집합 면적
    inter_area = (inter_xmax - inter_xmin) * (inter_ymax - inter_ymin)

    # 각 박스의 면적
    box1_area = (x1_max - x1_min) * (y1_max - y1_min)
    box2_area = (x2_max - x2_min) * (y2_max - y2_min)

    # 합집합 면적
    union_area = box1_area + box2_area - inter_area

    # IoU 계산
    if union_area == 0:
        return 0.0

    iou = inter_area / union_area
    return iou


def find_best_match(target_box: List[int], candidate_boxes: List[Dict],
                     iou_threshold: float = 0.5) -> Optional[int]:
    """
    target_box와 가장 잘 매칭되는 candidate_box 찾기

    Args:
        target_box: 매칭할 bbox [ymin, xmin, ymax, xmax]
        candidate_boxes: 후보 bbox 리스트
        iou_threshold: 매칭으로 간주할 최소 IoU

    Returns:
        int: 매칭된 candidate_boxes의 인덱스, 없으면 None
    """
    best_iou = 0.0
    best_idx = None

    for idx, candidate in enumerate(candidate_boxes):
        candidate_box = candidate["box_2d"]
        iou = calculate_iou(target_box, candidate_box)

        if iou > best_iou and iou >= iou_threshold:
            best_iou = iou
            best_idx = idx

    return best_idx


def merge_bboxes(cv_boxes: List[Dict], gemini_boxes: List[Dict],
                 iou_threshold: float = 0.5) -> List[Dict]:
    """
    Cloud Vision과 Gemini의 bbox를 병합

    병합 전략:
    1. IoU > threshold → 같은 텍스트 (CV 좌표 + Gemini 속성 사용)
    2. CV에만 있음 → CV 사용 (기본값: normal, in_bubble)
    3. Gemini에만 있음 → Gemini 사용 (CV가 놓친 텍스트)

    Args:
        cv_boxes: Cloud Vision bbox 리스트
        gemini_boxes: Gemini bbox 리스트
        iou_threshold: 매칭 기준 IoU

    Returns:
        List[Dict]: 병합된 bbox 리스트
    """
    merged = []
    gemini_matched = set()  # Gemini에서 매칭된 인덱스 추적

    print(f"\n🔄 BBox 병합 시작:")
    print(f"  Cloud Vision: {len(cv_boxes)}개")
    print(f"  Gemini: {len(gemini_boxes)}개")

    # 1. Cloud Vision bbox를 기준으로 Gemini와 매칭
    for cv_box in cv_boxes:
        cv_bbox = cv_box["box_2d"]
        cv_text = cv_box.get("label", "")

        # Gemini에서 매칭되는 bbox 찾기
        match_idx = find_best_match(cv_bbox, gemini_boxes, iou_threshold)

        if match_idx is not None:
            # 매칭 성공: CV 좌표 + Gemini 속성
            gemini_box = gemini_boxes[match_idx]
            gemini_matched.add(match_idx)

            merged_box = {
                "box_2d": cv_bbox,  # Cloud Vision 좌표 (정확함)
                "label": gemini_box.get("label", cv_text),  # Gemini OCR (더 정확)
                "bubble_type": gemini_box.get("bubble_type", "in_bubble"),
                "font_style": gemini_box.get("font_style", "normal"),
                "source": "matched",
                "confidence": cv_box.get("confidence", 1.0)
            }
            merged.append(merged_box)
            print(f"  ✓ 매칭: CV '{cv_text}' ↔ Gemini '{gemini_box.get('label', '')}'")
        else:
            # CV에만 있음: 기본값 사용
            merged_box = {
                "box_2d": cv_bbox,
                "label": cv_text,
                "bubble_type": "in_bubble",  # 기본값
                "font_style": "normal",  # 기본값
                "source": "cv_only",
                "confidence": cv_box.get("confidence", 1.0)
            }
            merged.append(merged_box)
            print(f"  ○ CV만: '{cv_text}' (기본값 적용)")

    # 2. Gemini에만 있는 bbox 추가 (CV가 놓친 텍스트)
    for idx, gemini_box in enumerate(gemini_boxes):
        if idx not in gemini_matched:
            merged_box = {
                "box_2d": gemini_box["box_2d"],
                "label": gemini_box.get("label", ""),
                "bubble_type": gemini_box.get("bubble_type", "in_bubble"),
                "font_style": gemini_box.get("font_style", "normal"),
                "source": "gemini_only",
                "confidence": 0.8  # Gemini bbox는 신뢰도 낮음
            }
            merged.append(merged_box)
            print(f"  + Gemini만: '{gemini_box.get('label', '')}' (CV 누락)")

    print(f"\n✅ 병합 완료: {len(merged)}개 bbox")
    print(f"  - 매칭: {sum(1 for b in merged if b['source'] == 'matched')}개")
    print(f"  - CV만: {sum(1 for b in merged if b['source'] == 'cv_only')}개")
    print(f"  - Gemini만: {sum(1 for b in merged if b['source'] == 'gemini_only')}개")

    return merged


def compare_bboxes(cv_boxes: List[Dict], gemini_boxes: List[Dict]) -> Dict:
    """
    Cloud Vision과 Gemini bbox를 비교하여 통계 생성

    Args:
        cv_boxes: Cloud Vision bbox 리스트
        gemini_boxes: Gemini bbox 리스트

    Returns:
        Dict: 비교 통계
    """
    stats = {
        "cv_count": len(cv_boxes),
        "gemini_count": len(gemini_boxes),
        "matched_count": 0,
        "cv_only_count": 0,
        "gemini_only_count": 0,
        "avg_iou": 0.0
    }

    gemini_matched = set()
    ious = []

    for cv_box in cv_boxes:
        cv_bbox = cv_box["box_2d"]
        match_idx = find_best_match(cv_bbox, gemini_boxes, iou_threshold=0.3)

        if match_idx is not None:
            stats["matched_count"] += 1
            gemini_matched.add(match_idx)
            iou = calculate_iou(cv_bbox, gemini_boxes[match_idx]["box_2d"])
            ious.append(iou)
        else:
            stats["cv_only_count"] += 1

    stats["gemini_only_count"] = len(gemini_boxes) - len(gemini_matched)
    stats["avg_iou"] = sum(ious) / len(ious) if ious else 0.0

    return stats


def is_bbox_contained(inner_box: List[int], outer_box: List[int], threshold: float = 0.7) -> bool:
    """
    inner_box가 outer_box 안에 포함되는지 확인

    Args:
        inner_box: [ymin, xmin, ymax, xmax]
        outer_box: [ymin, xmin, ymax, xmax]
        threshold: 포함으로 간주할 최소 비율 (inner의 몇 %가 outer에 포함되는지)

    Returns:
        bool: 포함 여부
    """
    y1_min, x1_min, y1_max, x1_max = inner_box
    y2_min, x2_min, y2_max, x2_max = outer_box

    # 교집합 영역 계산
    inter_xmin = max(x1_min, x2_min)
    inter_ymin = max(y1_min, y2_min)
    inter_xmax = min(x1_max, x2_max)
    inter_ymax = min(y1_max, y2_max)

    # 교집합이 없는 경우
    if inter_xmin >= inter_xmax or inter_ymin >= inter_ymax:
        return False

    # 교집합 면적
    inter_area = (inter_xmax - inter_xmin) * (inter_ymax - inter_ymin)

    # inner_box 면적
    inner_area = (x1_max - x1_min) * (y1_max - y1_min)

    # 포함 비율 계산
    if inner_area == 0:
        return False

    overlap_ratio = inter_area / inner_area

    return overlap_ratio >= threshold


def calculate_union_bbox(boxes: List[List[int]]) -> List[int]:
    """
    여러 bbox의 union (전체를 감싸는 최소 사각형) 계산

    Args:
        boxes: [[ymin, xmin, ymax, xmax], ...] 리스트

    Returns:
        List[int]: [ymin, xmin, ymax, xmax] - union bbox
    """
    if not boxes:
        return [0, 0, 0, 0]

    all_ymins = [box[0] for box in boxes]
    all_xmins = [box[1] for box in boxes]
    all_ymaxs = [box[2] for box in boxes]
    all_xmaxs = [box[3] for box in boxes]

    ymin = min(all_ymins)
    xmin = min(all_xmins)
    ymax = max(all_ymaxs)
    xmax = max(all_xmaxs)

    return [ymin, xmin, ymax, xmax]


def expand_bbox_by_margin(bbox: List[int], margin: int = 20) -> List[int]:
    """
    bbox를 margin만큼 확장

    Args:
        bbox: [ymin, xmin, ymax, xmax]
        margin: 확장 픽셀 (기본 20)

    Returns:
        List[int]: 확장된 bbox
    """
    ymin, xmin, ymax, xmax = bbox
    return [
        max(0, ymin - margin),
        max(0, xmin - margin),
        ymax + margin,
        xmax + margin
    ]


def is_point_in_bbox(point: Tuple[int, int], bbox: List[int]) -> bool:
    """
    점이 bbox 내에 있는지 확인

    Args:
        point: (y, x)
        bbox: [ymin, xmin, ymax, xmax]

    Returns:
        bool: 포함 여부
    """
    y, x = point
    ymin, xmin, ymax, xmax = bbox
    return ymin <= y <= ymax and xmin <= x <= xmax


def is_valid_cv_bbox(bbox: List[int], min_size: int = 5) -> bool:
    """
    CV bbox가 유효한지 확인 (너무 작은 bbox 필터링)

    Args:
        bbox: [ymin, xmin, ymax, xmax]
        min_size: 최소 크기 (기본 5픽셀)

    Returns:
        bool: 유효 여부
    """
    ymin, xmin, ymax, xmax = bbox
    height = ymax - ymin
    width = xmax - xmin
    return height >= min_size and width >= min_size


def has_nearby_bbox(target_bbox: List[int], other_bboxes: List[List[int]],
                    distance_threshold: int = 50) -> bool:
    """
    target_bbox 근처에 다른 bbox가 있는지 확인

    Args:
        target_bbox: [ymin, xmin, ymax, xmax]
        other_bboxes: 다른 bbox 리스트
        distance_threshold: 근처로 간주할 거리 (기본 50픽셀)

    Returns:
        bool: 근처에 bbox가 있으면 True
    """
    t_center = calculate_bbox_center(target_bbox)

    for other_bbox in other_bboxes:
        o_center = calculate_bbox_center(other_bbox)

        # 유클리드 거리 계산
        distance = ((t_center[0] - o_center[0])**2 + (t_center[1] - o_center[1])**2)**0.5

        if distance <= distance_threshold:
            return True

    return False


def cluster_cv_bboxes_in_region(cv_boxes: List[Dict], region_bbox: List[int],
                                 x_threshold: int = 15, y_threshold: int = 8,
                                 min_bbox_size: int = 5,
                                 filter_isolated: bool = True,
                                 isolation_distance: int = 50) -> List[Dict]:
    """
    특정 영역 내의 CV bbox만 클러스터링 (작은 노이즈 필터링 포함)

    Args:
        cv_boxes: 전체 CV bbox 리스트
        region_bbox: 클러스터링 영역 [ymin, xmin, ymax, xmax]
        x_threshold: x 좌표 인접 기준
        y_threshold: y 좌표 인접 기준
        min_bbox_size: 최소 bbox 크기 (기본 5픽셀)

    Returns:
        List[Dict]: 영역 내 CV bbox 클러스터링 결과
    """
    # 1. 영역 내 CV bbox 필터링 (중심점 기준 + 크기 필터링)
    region_cv_boxes = []
    for box in cv_boxes:
        # 크기 검증
        if not is_valid_cv_bbox(box["box_2d"], min_bbox_size):
            continue

        # 영역 내 포함 검증
        center = calculate_bbox_center(box["box_2d"])
        if is_point_in_bbox(center, region_bbox):
            region_cv_boxes.append(box)

    if not region_cv_boxes:
        return []

    # 2. 1글자 bbox만 고립 여부 판단하여 필터링 (옵션)
    if filter_isolated:
        filtered_boxes = []
        all_bboxes = [box["box_2d"] for box in region_cv_boxes]

        for i, box in enumerate(region_cv_boxes):
            label = box.get("label", "")

            # 2글자 이상이면 무조건 유지
            if len(label) >= 2:
                filtered_boxes.append(box)
                continue

            # 1글자인 경우만 고립 여부 확인
            # 현재 bbox를 제외한 다른 bbox 리스트
            other_bboxes = [bbox for j, bbox in enumerate(all_bboxes) if j != i]

            # 근처에 다른 bbox가 있으면 유지
            if has_nearby_bbox(box["box_2d"], other_bboxes, isolation_distance):
                filtered_boxes.append(box)
            # else: 고립된 1글자 bbox는 버림

        region_cv_boxes = filtered_boxes

        if not region_cv_boxes:
            return []

    # 3. 세로 병합 (x1 기준)
    sorted_by_x = sorted(region_cv_boxes, key=lambda b: (b["box_2d"][1], b["box_2d"][0]))

    vertical_clusters = []
    current_cluster = []

    for box in sorted_by_x:
        if not current_cluster:
            current_cluster.append(box)
        else:
            last_bbox = current_cluster[-1]["box_2d"]
            curr_bbox = box["box_2d"]

            x_diff = abs(curr_bbox[1] - last_bbox[1])
            y_gap = curr_bbox[0] - last_bbox[2]

            if x_diff <= x_threshold and y_gap <= y_threshold:
                current_cluster.append(box)
            else:
                vertical_clusters.append(current_cluster)
                current_cluster = [box]

    if current_cluster:
        vertical_clusters.append(current_cluster)

    # 세로 클러스터를 bbox로 변환
    vertical_boxes = []
    for cluster in vertical_clusters:
        if len(cluster) == 1:
            vertical_boxes.append(cluster[0])
        else:
            bboxes = [b["box_2d"] for b in cluster]
            union_bbox = calculate_union_bbox(bboxes)
            labels = [b.get("label", "") for b in cluster]
            vertical_boxes.append({
                "box_2d": union_bbox,
                "label": "".join(labels),
                "confidence": cluster[0].get("confidence", 1.0)
            })

    # 4. 가로 병합 (y1 기준)
    sorted_by_y = sorted(vertical_boxes, key=lambda b: (b["box_2d"][0], b["box_2d"][1]))

    horizontal_clusters = []
    current_cluster = []

    for box in sorted_by_y:
        if not current_cluster:
            current_cluster.append(box)
        else:
            last_bbox = current_cluster[-1]["box_2d"]
            curr_bbox = box["box_2d"]

            y_diff = abs(curr_bbox[0] - last_bbox[0])
            x_gap = curr_bbox[1] - last_bbox[3]

            if y_diff <= y_threshold and x_gap <= x_threshold:
                current_cluster.append(box)
            else:
                horizontal_clusters.append(current_cluster)
                current_cluster = [box]

    if current_cluster:
        horizontal_clusters.append(current_cluster)

    # 최종 병합
    final_boxes = []
    for cluster in horizontal_clusters:
        if len(cluster) == 1:
            final_boxes.append(cluster[0])
        else:
            bboxes = [b["box_2d"] for b in cluster]
            union_bbox = calculate_union_bbox(bboxes)
            labels = [b.get("label", "") for b in cluster]
            final_boxes.append({
                "box_2d": union_bbox,
                "label": "".join(labels),
                "confidence": cluster[0].get("confidence", 1.0)
            })

    return final_boxes


def cluster_cv_bboxes_by_line(cv_boxes: List[Dict], x_threshold: int = 30, y_threshold: int = 10) -> List[Dict]:
    """
    2단계 CV bbox 클러스터링

    1단계 (세로 우선): x1 동일 + y 인접 → 세로로 긴 bbox
    2단계 (가로 병합): y1 유사 + x 인접 → 최종 bbox

    Args:
        cv_boxes: CV bbox 리스트
        x_threshold: x 좌표 인접 기준 (픽셀, 기본 30)
        y_threshold: y 좌표 인접 기준 (픽셀, 기본 10)

    Returns:
        List[Dict]: 클러스터링된 bbox 리스트
    """
    if not cv_boxes:
        return []

    print(f"\n📦 CV bbox 2단계 클러스터링: {len(cv_boxes)}개")

    # ========================================
    # 1단계: 세로 병합 (x1 기준 정렬)
    # ========================================
    print("  1단계: 세로 병합 (x1 동일 + y 인접)")
    sorted_by_x = sorted(cv_boxes, key=lambda b: (b["box_2d"][1], b["box_2d"][0]))  # xmin, ymin

    vertical_clusters = []
    current_cluster = []

    for box in sorted_by_x:
        if not current_cluster:
            current_cluster.append(box)
        else:
            last_bbox = current_cluster[-1]["box_2d"]
            curr_bbox = box["box_2d"]

            # 세로 병합 조건: x1(xmin) 동일 + y 인접
            x_diff = abs(curr_bbox[1] - last_bbox[1])  # xmin 차이
            y_gap = curr_bbox[0] - last_bbox[2]  # curr ymin - last ymax

            if x_diff <= x_threshold and y_gap <= y_threshold:
                current_cluster.append(box)
            else:
                vertical_clusters.append(current_cluster)
                current_cluster = [box]

    if current_cluster:
        vertical_clusters.append(current_cluster)

    # 세로 클러스터를 bbox로 변환
    vertical_boxes = []
    for cluster in vertical_clusters:
        if len(cluster) == 1:
            vertical_boxes.append(cluster[0])
        else:
            bboxes = [b["box_2d"] for b in cluster]
            union_bbox = calculate_union_bbox(bboxes)
            labels = [b.get("label", "") for b in cluster]
            merged_box = {
                "box_2d": union_bbox,
                "label": "".join(labels),
                "confidence": cluster[0].get("confidence", 1.0),
                "cluster_size": len(cluster)
            }
            vertical_boxes.append(merged_box)

    print(f"    → {len(cv_boxes)}개 → {len(vertical_boxes)}개 (세로 병합)")

    # ========================================
    # 2단계: 가로 병합 (y1 기준 정렬)
    # ========================================
    print("  2단계: 가로 병합 (y1 유사 + x 인접)")
    sorted_by_y = sorted(vertical_boxes, key=lambda b: (b["box_2d"][0], b["box_2d"][1]))  # ymin, xmin

    horizontal_clusters = []
    current_cluster = []

    for box in sorted_by_y:
        if not current_cluster:
            current_cluster.append(box)
        else:
            last_bbox = current_cluster[-1]["box_2d"]
            curr_bbox = box["box_2d"]

            # 가로 병합 조건: y1(ymin) 유사 + x 인접
            y_diff = abs(curr_bbox[0] - last_bbox[0])  # ymin 차이
            x_gap = curr_bbox[1] - last_bbox[3]  # curr xmin - last xmax

            if y_diff <= y_threshold and x_gap <= x_threshold:
                current_cluster.append(box)
            else:
                horizontal_clusters.append(current_cluster)
                current_cluster = [box]

    if current_cluster:
        horizontal_clusters.append(current_cluster)

    # 최종 병합
    final_boxes = []
    for idx, cluster in enumerate(horizontal_clusters, 1):
        if len(cluster) == 1:
            final_boxes.append(cluster[0])
        else:
            bboxes = [b["box_2d"] for b in cluster]
            union_bbox = calculate_union_bbox(bboxes)
            labels = [b.get("label", "") for b in cluster]
            merged_label = "".join(labels)

            merged_box = {
                "box_2d": union_bbox,
                "label": merged_label,
                "confidence": cluster[0].get("confidence", 1.0),
                "cluster_size": len(cluster)
            }
            final_boxes.append(merged_box)
            print(f"    클러스터 #{idx}: {len(cluster)}개 병합 → '{merged_label}'")

    print(f"    → {len(vertical_boxes)}개 → {len(final_boxes)}개 (가로 병합)")
    print(f"✅ 최종: {len(cv_boxes)}개 → {len(final_boxes)}개")

    return final_boxes


def calculate_bbox_ratio(bbox: List[int]) -> float:
    """
    bbox의 가로세로 비율 계산 (width/height)

    Args:
        bbox: [ymin, xmin, ymax, xmax]

    Returns:
        float: width/height 비율
    """
    ymin, xmin, ymax, xmax = bbox
    height = ymax - ymin
    width = xmax - xmin

    if height == 0:
        return 1.0

    return width / height


def expand_bbox_with_ratio(bbox: List[int], target_ratio: float) -> List[int]:
    """
    bbox의 중심은 유지하되, target_ratio (width/height)로 확장

    Args:
        bbox: [ymin, xmin, ymax, xmax]
        target_ratio: 목표 가로세로 비율 (width/height)

    Returns:
        List[int]: 확장된 bbox
    """
    ymin, xmin, ymax, xmax = bbox
    height = ymax - ymin
    width = xmax - xmin

    center_y = (ymin + ymax) // 2
    center_x = (xmin + xmax) // 2

    current_ratio = width / height if height > 0 else 1

    # Gemini 비율로 확장
    if target_ratio > current_ratio:
        # width를 늘려야 함 (가로가 상대적으로 더 김)
        new_width = int(height * target_ratio)
        new_height = height
    else:
        # height를 늘려야 함 (세로가 상대적으로 더 김)
        new_height = int(width / target_ratio)
        new_width = width

    new_ymin = center_y - new_height // 2
    new_ymax = new_ymin + new_height
    new_xmin = center_x - new_width // 2
    new_xmax = new_xmin + new_width

    return [new_ymin, new_xmin, new_ymax, new_xmax]


def has_overlap(box1: List[int], box2: List[int], threshold: float = 0.1) -> bool:
    """
    두 bbox가 오버랩되는지 확인

    Args:
        box1: [ymin, xmin, ymax, xmax]
        box2: [ymin, xmin, ymax, xmax]
        threshold: 최소 IoU threshold

    Returns:
        bool: 오버랩 여부
    """
    iou = calculate_iou(box1, box2)
    return iou >= threshold


def calculate_bbox_center(bbox: List[int]) -> Tuple[int, int]:
    """
    bbox의 중심점 계산

    Args:
        bbox: [ymin, xmin, ymax, xmax]

    Returns:
        Tuple[int, int]: (center_y, center_x)
    """
    ymin, xmin, ymax, xmax = bbox
    center_y = (ymin + ymax) // 2
    center_x = (xmin + xmax) // 2
    return (center_y, center_x)


def adjust_bbox_to_center(bbox: List[int], target_center: Tuple[int, int]) -> List[int]:
    """
    bbox의 크기는 유지하되, 중심점을 target_center로 이동

    Args:
        bbox: [ymin, xmin, ymax, xmax]
        target_center: (center_y, center_x)

    Returns:
        List[int]: 조정된 bbox
    """
    ymin, xmin, ymax, xmax = bbox
    height = ymax - ymin
    width = xmax - xmin

    target_y, target_x = target_center

    new_ymin = target_y - height // 2
    new_ymax = new_ymin + height
    new_xmin = target_x - width // 2
    new_xmax = new_xmin + width

    return [new_ymin, new_xmin, new_ymax, new_xmax]


def constrain_bbox_to_region(bbox: List[int], constraint: List[int], margin: int = 10) -> List[int]:
    """
    bbox를 constraint 영역에 맞게 제한 (margin 허용)

    Args:
        bbox: [ymin, xmin, ymax, xmax]
        constraint: [ymin, xmin, ymax, xmax] 제약 영역
        margin: 허용 마진 (기본 10픽셀)

    Returns:
        List[int]: 제한된 bbox
    """
    ymin, xmin, ymax, xmax = bbox
    c_ymin, c_xmin, c_ymax, c_xmax = constraint

    # Y 좌표 제한 (위아래로 margin 허용)
    constrained_ymin = max(ymin, c_ymin - margin)
    constrained_ymax = min(ymax, c_ymax + margin)

    # X 좌표 제한 (좌우로 margin 허용)
    constrained_xmin = max(xmin, c_xmin - margin)
    constrained_xmax = min(xmax, c_xmax + margin)

    return [constrained_ymin, constrained_xmin, constrained_ymax, constrained_xmax]


def merge_bboxes_regional_cluster(cv_boxes: List[Dict], gemini_boxes: List[Dict],
                                   expand_margin: int = 30,
                                   x_threshold: int = 15, y_threshold: int = 8,
                                   min_cv_count: int = 3,
                                   constrain_margin: int = 20) -> List[Dict]:
    """
    Gemini 영역별 CV 클러스터링 + 개선된 bbox 보정

    전략:
    1. 각 Gemini bbox를 margin만큼 확장
    2. 확장된 영역 내의 유효한 CV bbox들만 클러스터링
    3. CV가 충분하면 → CV union + Gemini 비율 확장 + Y좌표 제한
    4. CV가 부족하면 → Gemini bbox 사용 (CV 미감지 또는 오감지)
    5. Gemini OCR + bubble_type + font_style 사용

    Args:
        cv_boxes: Cloud Vision bbox 리스트
        gemini_boxes: Gemini bbox 리스트
        expand_margin: Gemini bbox 확장 픽셀 (기본 30)
        x_threshold: 클러스터링 x 기준 (기본 15)
        y_threshold: 클러스터링 y 기준 (기본 8)
        min_cv_count: CV bbox 최소 개수 (기본 3, 이하면 Gemini 사용)
        constrain_margin: Y좌표 제한 마진 (기본 20)

    Returns:
        List[Dict]: 병합된 bbox 리스트
    """
    merged = []

    print(f"\n🔄 영역별 CV 클러스터링 + 개선된 bbox 보정:")
    print(f"  Cloud Vision: {len(cv_boxes)}개")
    print(f"  Gemini: {len(gemini_boxes)}개")
    print(f"  확장 margin: {expand_margin}px, CV 최소: {min_cv_count}개")

    for idx, gemini_box in enumerate(gemini_boxes, 1):
        gemini_bbox = gemini_box["box_2d"]
        gemini_text = gemini_box.get("label", "")

        # 1. Gemini bbox 확장
        expanded_region = expand_bbox_by_margin(gemini_bbox, expand_margin)

        # 2. 확장 영역 내 CV bbox 클러스터링
        clustered = cluster_cv_bboxes_in_region(cv_boxes, expanded_region, x_threshold, y_threshold, min_bbox_size=5)

        if clustered and len(clustered) >= min_cv_count:
            # 3. CV가 충분함 → CV union + Gemini 비율 + Y좌표 제한
            cv_bboxes = [b["box_2d"] for b in clustered]
            cv_union = calculate_union_bbox(cv_bboxes)

            # Gemini 비율로 확장
            gemini_ratio = calculate_bbox_ratio(gemini_bbox)
            expanded_bbox = expand_bbox_with_ratio(cv_union, gemini_ratio)

            # Y좌표를 Gemini 기준으로 제한 (과대추정 방지)
            final_bbox = constrain_bbox_to_region(expanded_bbox, gemini_bbox, constrain_margin)

            merged_box = {
                "box_2d": final_bbox,
                "label": gemini_text,
                "bubble_type": gemini_box.get("bubble_type", "in_bubble"),
                "font_style": gemini_box.get("font_style", "normal"),
                "source": "cv_based",
                "cv_count": len(clustered),
                "gemini_ratio": round(gemini_ratio, 2),
                "confidence": 0.95
            }
            merged.append(merged_box)
            print(f"  ✓ #{idx} '{gemini_text}' (CV {len(clustered)}개, 비율 {gemini_ratio:.2f})")
        else:
            # 4. CV가 부족 → Gemini bbox + CV가 있으면 aspect ratio로 조정
            final_bbox = gemini_bbox

            if clustered:
                # CV bbox가 있지만 부족한 경우 - CV union + Gemini aspect ratio
                cv_bboxes = [b["box_2d"] for b in clustered]
                cv_union = calculate_union_bbox(cv_bboxes)

                # Gemini aspect ratio로 확장
                gemini_ratio = calculate_bbox_ratio(gemini_bbox)
                expanded_bbox = expand_bbox_with_ratio(cv_union, gemini_ratio)

                # Y좌표 제한
                final_bbox = constrain_bbox_to_region(expanded_bbox, gemini_bbox, constrain_margin)

                cv_note = f"CV {len(clustered)}개 → Gemini 비율 적용"
            else:
                cv_note = "CV 없음, Gemini bbox 사용"

            merged_box = {
                "box_2d": final_bbox,
                "label": gemini_text,
                "bubble_type": gemini_box.get("bubble_type", "in_bubble"),
                "font_style": gemini_box.get("font_style", "normal"),
                "source": "gemini_based",
                "cv_count": len(clustered) if clustered else 0,
                "gemini_ratio": round(calculate_bbox_ratio(gemini_bbox), 2) if clustered else None,
                "confidence": 0.85
            }
            merged.append(merged_box)
            print(f"  + #{idx} '{gemini_text}' ({cv_note})")

    print(f"\n✅ 영역별 클러스터링 완료: {len(merged)}개")
    print(f"  - CV기반: {sum(1 for b in merged if b['source'] == 'cv_based')}개")
    print(f"  - Gemini기반: {sum(1 for b in merged if b['source'] == 'gemini_based')}개")

    return merged


def merge_bboxes_cv_expanded(cv_boxes: List[Dict], gemini_boxes: List[Dict],
                             overlap_threshold: float = 0.1) -> List[Dict]:
    """
    CV bbox 기반 + Gemini 비율로 확장

    전략:
    1. Gemini bbox와 오버랩되는 CV bbox들 찾기
    2. CV bbox들의 union 계산 (정확한 좌표)
    3. Gemini bbox의 가로세로 비율 계산
    4. CV union을 Gemini 비율로 확장
    5. CV와 오버랩 없는 Gemini bbox는 추가 (CV가 놓친 부분)

    Args:
        cv_boxes: Cloud Vision bbox 리스트
        gemini_boxes: Gemini bbox 리스트
        overlap_threshold: 오버랩으로 간주할 최소 IoU (기본 0.1)

    Returns:
        List[Dict]: 병합된 bbox 리스트
    """
    merged = []
    cv_used = set()

    print(f"\n🔄 CV 기반 + Gemini 비율 확장 시작:")
    print(f"  Cloud Vision: {len(cv_boxes)}개")
    print(f"  Gemini: {len(gemini_boxes)}개")

    # Gemini bbox를 기준으로 CV bbox 그룹화 및 확장
    for gemini_box in gemini_boxes:
        gemini_bbox = gemini_box["box_2d"]
        gemini_text = gemini_box.get("label", "")

        # Gemini와 오버랩되는 CV bbox들 찾기
        overlapping_cv_indices = []
        overlapping_cv_boxes = []

        for idx, cv_box in enumerate(cv_boxes):
            cv_bbox = cv_box["box_2d"]
            if has_overlap(cv_bbox, gemini_bbox, overlap_threshold):
                overlapping_cv_indices.append(idx)
                overlapping_cv_boxes.append(cv_bbox)
                cv_used.add(idx)

        if overlapping_cv_boxes:
            # CV bbox들의 union (정확한 좌표)
            cv_union = calculate_union_bbox(overlapping_cv_boxes)

            # Gemini 비율로 CV union 확장
            gemini_ratio = calculate_bbox_ratio(gemini_bbox)
            expanded_bbox = expand_bbox_with_ratio(cv_union, gemini_ratio)

            merged_box = {
                "box_2d": expanded_bbox,  # CV 좌표 + Gemini 비율
                "label": gemini_text,  # Gemini OCR
                "bubble_type": gemini_box.get("bubble_type", "in_bubble"),
                "font_style": gemini_box.get("font_style", "normal"),
                "source": "cv_expanded",
                "cv_count": len(overlapping_cv_boxes),
                "gemini_ratio": round(gemini_ratio, 2),
                "confidence": 0.95
            }
            merged.append(merged_box)
            print(f"  ✓ CV확장: '{gemini_text}' (CV {len(overlapping_cv_boxes)}개, 비율 {gemini_ratio:.2f})")
        else:
            # CV와 오버랩 없음 = CV가 놓친 부분
            merged_box = {
                "box_2d": gemini_bbox,
                "label": gemini_text,
                "bubble_type": gemini_box.get("bubble_type", "in_bubble"),
                "font_style": gemini_box.get("font_style", "normal"),
                "source": "gemini_only",
                "cv_count": 0,
                "confidence": 0.8
            }
            merged.append(merged_box)
            print(f"  + Gemini만: '{gemini_text}' (CV 오버랩 없음)")

    print(f"\n✅ CV 기반 확장 완료: {len(merged)}개 bbox")
    print(f"  - CV확장: {sum(1 for b in merged if b['source'] == 'cv_expanded')}개")
    print(f"  - Gemini만: {sum(1 for b in merged if b['source'] == 'gemini_only')}개")
    print(f"  - CV only 제외 (노이즈 가능성)")

    return merged


def merge_bboxes_refined(cv_boxes: List[Dict], gemini_boxes: List[Dict],
                         containment_threshold: float = 0.3) -> List[Dict]:
    """
    개선된 Hybrid 병합: Gemini bbox 크기 유지 + CV 좌표로 위치 미세조정

    전략:
    1. Gemini bbox를 기준으로 사용 (텍스트 영역의 크기/비율 신뢰)
    2. 해당 영역 내의 CV bbox들을 찾음 (낮은 threshold로 더 많이 포함)
    3. CV bbox들의 중심점 평균으로 Gemini bbox 위치 미세조정
    4. Gemini가 감지한 것만 사용 (CV only는 노이즈 가능성 높아 제외)

    Args:
        cv_boxes: Cloud Vision bbox 리스트
        gemini_boxes: Gemini bbox 리스트
        containment_threshold: CV bbox가 Gemini 안에 포함된 것으로 간주할 최소 비율 (기본 0.3)

    Returns:
        List[Dict]: 병합된 bbox 리스트
    """
    merged = []

    print(f"\n🔄 개선된 Hybrid 병합 시작:")
    print(f"  Cloud Vision: {len(cv_boxes)}개")
    print(f"  Gemini: {len(gemini_boxes)}개")

    # Gemini bbox를 기준으로 처리
    for gem_idx, gemini_box in enumerate(gemini_boxes):
        gemini_bbox = gemini_box["box_2d"]
        gemini_text = gemini_box.get("label", "")

        # Gemini 영역 내의 CV bbox들 찾기 (threshold 낮춤)
        contained_cv_boxes = []
        for cv_box in cv_boxes:
            cv_bbox = cv_box["box_2d"]
            if is_bbox_contained(cv_bbox, gemini_bbox, containment_threshold):
                contained_cv_boxes.append(cv_bbox)

        if contained_cv_boxes:
            # CV bbox들의 중심점 평균 계산
            center_ys = [calculate_bbox_center(box)[0] for box in contained_cv_boxes]
            center_xs = [calculate_bbox_center(box)[1] for box in contained_cv_boxes]
            avg_center = (sum(center_ys) // len(center_ys), sum(center_xs) // len(center_xs))

            # Gemini bbox를 CV 중심으로 미세조정
            adjusted_bbox = adjust_bbox_to_center(gemini_bbox, avg_center)

            merged_box = {
                "box_2d": adjusted_bbox,  # Gemini 크기 + CV 위치
                "label": gemini_text,  # Gemini OCR
                "bubble_type": gemini_box.get("bubble_type", "in_bubble"),
                "font_style": gemini_box.get("font_style", "normal"),
                "source": "refined",
                "cv_count": len(contained_cv_boxes),
                "confidence": 0.95
            }
            merged.append(merged_box)
            print(f"  ✓ Refined: '{gemini_text}' (CV {len(contained_cv_boxes)}개로 위치조정)")
        else:
            # CV bbox 없으면 Gemini bbox 그대로 사용
            merged_box = {
                "box_2d": gemini_bbox,
                "label": gemini_text,
                "bubble_type": gemini_box.get("bubble_type", "in_bubble"),
                "font_style": gemini_box.get("font_style", "normal"),
                "source": "gemini_only",
                "cv_count": 0,
                "confidence": 0.8
            }
            merged.append(merged_box)
            print(f"  + Gemini만: '{gemini_text}' (CV 없음, Gemini bbox 사용)")

    print(f"\n✅ 개선된 Hybrid 병합 완료: {len(merged)}개 bbox")
    print(f"  - Refined: {sum(1 for b in merged if b['source'] == 'refined')}개")
    print(f"  - Gemini만: {sum(1 for b in merged if b['source'] == 'gemini_only')}개")
    print(f"  - CV only 제외 (노이즈 가능성)")

    return merged


def merge_bboxes_hybrid(cv_boxes: List[Dict], gemini_boxes: List[Dict],
                        containment_threshold: float = 0.5) -> List[Dict]:
    """
    Hybrid 병합: Gemini 기준 + CV 좌표 보정

    전략:
    1. Gemini bbox를 기준 영역으로 사용
    2. 각 Gemini 영역 내의 CV bbox들을 모두 수집
    3. CV bbox들의 union으로 정확한 좌표 생성
    4. Gemini OCR + bubble_type + font_style 사용
    5. Gemini가 놓친 CV bbox들은 별도로 추가

    Args:
        cv_boxes: Cloud Vision bbox 리스트
        gemini_boxes: Gemini bbox 리스트
        containment_threshold: CV bbox가 Gemini 안에 포함된 것으로 간주할 최소 비율

    Returns:
        List[Dict]: 병합된 bbox 리스트
    """
    merged = []
    cv_used = set()  # 사용된 CV bbox 인덱스 추적

    print(f"\n🔄 Hybrid 병합 시작:")
    print(f"  Cloud Vision: {len(cv_boxes)}개")
    print(f"  Gemini: {len(gemini_boxes)}개")

    # 1. Gemini bbox를 기준으로 CV bbox들 수집 및 병합
    for gem_idx, gemini_box in enumerate(gemini_boxes):
        gemini_bbox = gemini_box["box_2d"]
        gemini_text = gemini_box.get("label", "")

        # Gemini 영역 내의 CV bbox들 찾기
        contained_cv_indices = []
        contained_cv_boxes = []

        for cv_idx, cv_box in enumerate(cv_boxes):
            cv_bbox = cv_box["box_2d"]
            if is_bbox_contained(cv_bbox, gemini_bbox, containment_threshold):
                contained_cv_indices.append(cv_idx)
                contained_cv_boxes.append(cv_bbox)
                cv_used.add(cv_idx)

        if contained_cv_boxes:
            # CV bbox들의 union으로 정확한 좌표 생성
            union_bbox = calculate_union_bbox(contained_cv_boxes)

            merged_box = {
                "box_2d": union_bbox,  # CV의 정확한 좌표
                "label": gemini_text,  # Gemini OCR
                "bubble_type": gemini_box.get("bubble_type", "in_bubble"),
                "font_style": gemini_box.get("font_style", "normal"),
                "source": "hybrid",
                "cv_count": len(contained_cv_boxes),  # 몇 개의 CV bbox 사용했는지
                "confidence": 0.95
            }
            merged.append(merged_box)
            print(f"  ✓ Hybrid: '{gemini_text}' (CV {len(contained_cv_boxes)}개 통합)")
        else:
            # Gemini 영역에 CV bbox가 없음 (Gemini만 감지)
            merged_box = {
                "box_2d": gemini_bbox,
                "label": gemini_text,
                "bubble_type": gemini_box.get("bubble_type", "in_bubble"),
                "font_style": gemini_box.get("font_style", "normal"),
                "source": "gemini_only",
                "cv_count": 0,
                "confidence": 0.7
            }
            merged.append(merged_box)
            print(f"  + Gemini만: '{gemini_text}' (CV 없음)")

    # 2. Gemini가 놓친 CV bbox들 추가
    for cv_idx, cv_box in enumerate(cv_boxes):
        if cv_idx not in cv_used:
            cv_text = cv_box.get("label", "")
            merged_box = {
                "box_2d": cv_box["box_2d"],
                "label": cv_text,
                "bubble_type": "in_bubble",  # 기본값
                "font_style": "normal",  # 기본값
                "source": "cv_only",
                "cv_count": 1,
                "confidence": cv_box.get("confidence", 1.0)
            }
            merged.append(merged_box)
            print(f"  ○ CV만: '{cv_text}' (Gemini 놓침)")

    print(f"\n✅ Hybrid 병합 완료: {len(merged)}개 bbox")
    print(f"  - Hybrid: {sum(1 for b in merged if b['source'] == 'hybrid')}개")
    print(f"  - Gemini만: {sum(1 for b in merged if b['source'] == 'gemini_only')}개")
    print(f"  - CV만: {sum(1 for b in merged if b['source'] == 'cv_only')}개")

    return merged


# 테스트 코드
if __name__ == "__main__":
    # IoU 테스트
    box1 = [100, 100, 200, 200]  # ymin, xmin, ymax, xmax
    box2 = [150, 150, 250, 250]  # 부분 겹침
    box3 = [300, 300, 400, 400]  # 완전 분리

    print("IoU 테스트:")
    print(f"  box1 vs box2 (부분 겹침): {calculate_iou(box1, box2):.3f}")
    print(f"  box1 vs box3 (완전 분리): {calculate_iou(box1, box3):.3f}")
    print(f"  box1 vs box1 (동일): {calculate_iou(box1, box1):.3f}")

    # 병합 테스트
    cv_boxes = [
        {"box_2d": [100, 100, 200, 200], "label": "텍스트1"},
        {"box_2d": [300, 300, 400, 400], "label": "텍스트2"},
    ]

    gemini_boxes = [
        {"box_2d": [105, 105, 205, 205], "label": "텍스트1", "bubble_type": "in_bubble", "font_style": "normal"},
        {"box_2d": [500, 500, 600, 600], "label": "텍스트3", "bubble_type": "on_background", "font_style": "stylized"},
    ]

    print("\n병합 테스트:")
    merged = merge_bboxes(cv_boxes, gemini_boxes)
    for box in merged:
        print(f"  {box}")
