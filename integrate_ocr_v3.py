#!/usr/bin/env python3
"""
OCR 결과 통합 V3
Sentence (Gemini OCR) → Cluster (CV) Matching
개행 기반 청크(chunk) 단위로 라인별 정확한 매칭
"""
import json
import sys
import cv2
import numpy as np
from typing import List, Dict


def normalize_text(text: str) -> str:
    """
    텍스트 정규화 - 말줄임표 및 유사 기호 통일, 개행문자 처리

    - ... (3개 점) → …
    - ･･･ (일본어 중점 3개) → …
    - ・・・ → …
    - \n (개행) → 보존 (청크 분리용)
    - 기타 유사 기호들 통일
    """
    if not text:
        return text

    # 말줄임표 정규화
    text = text.replace('...', '…')  # 3개 점
    text = text.replace('．．．', '…')  # 전각 점 3개
    text = text.replace('。。。', '…')  # 마침표 3개
    text = text.replace('･･･', '…')  # 중점 3개
    text = text.replace('・・・', '…')  # 중점 3개
    text = text.replace('......', '…')  # 6개 점
    text = text.replace('・・', '…')  # 중점 2개
    text = text.replace('・・・・', '…')  # 중점 4개

    # 2개 점도 처리
    text = text.replace('..', '…')
    text = text.replace('･･', '…')

    # 기타 말줄임 기호
    text = text.replace('～～', '…')
    text = text.replace('~~', '…')

    return text


def split_sentence_into_chunks(text: str) -> List[str]:
    """
    Sentence를 개행 기준으로 청크(chunk)로 분리

    Args:
        text: "8글자\n8글자\n7글자" 형태의 텍스트

    Returns:
        ["8글자", "8글자", "7글자"] 형태의 청크 리스트
    """
    # 개행으로 분리하되, 빈 청크는 제거
    chunks = [chunk.strip() for chunk in text.split('\n') if chunk.strip()]
    return chunks


def calculate_chunk_overlap(chunk: str, cv_text: str) -> float:
    """
    개별 청크(chunk)와 CV 텍스트의 유사도 계산

    순서 기반 비교 (Longest Common Subsequence 아이디어 사용)

    Args:
        chunk: Sentence의 개별 청크 (예: "うわっ")
        cv_text: CV Line의 텍스트 (예: "うわ")

    Returns:
        유사도: 0.0 ~ 1.0
    """
    # 텍스트 정규화 (말줄임표 등 통일)
    chunk = normalize_text(chunk).strip()
    cv_text = normalize_text(cv_text).strip()

    # 공백/개행 제거 (비교용)
    chunk_clean = chunk.replace('\n', '').replace(' ', '').replace('　', '')
    cv_clean = cv_text.replace('\n', '').replace(' ', '').replace('　', '')

    if not chunk_clean or not cv_clean:
        return 0.0

    # 간단한 매칭: cv_text가 chunk에 포함되는지 여부
    # (CV가 놓친 글자가 있는 경우를 감지하기 위함)
    if cv_clean in chunk_clean:
        # CV 텍스트가 청크에 포함됨 (CV가 일부만 인식)
        return len(cv_clean) / len(chunk_clean)
    elif chunk_clean in cv_clean:
        # 청크가 CV에 포함됨 (CV가 과다 인식)
        return len(chunk_clean) / len(cv_clean)
    else:
        # 일부 겹침만 발생
        matches = sum(1 for char in chunk_clean if char in cv_clean)
        max_len = max(len(chunk_clean), len(cv_clean))
        return matches / max_len if max_len > 0 else 0.0


def match_chunks_to_cluster_lines(
    sentence_chunks: List[str],
    cv_lines: List[Dict]
) -> Dict:
    """
    Sentence의 청크들을 CV Cluster의 각 라인과 매칭

    Returns:
        {
            'matched': True/False,
            'line_matches': [
                {'cv_line_idx': 0, 'chunk_idx': 0, 'overlap': 0.95, 'missing_chars': []},
                ...
            ],
            'missing_chars_by_line': {'cv_line_0': ['char1', 'char2'], ...},
            'match_type': 'perfect' | 'partial' | 'none'
        }
    """
    line_matches = []
    missing_chars_by_line = {}

    # 각 CV 라인에 대해 최고 점수 청크 찾기
    for cv_line_idx, cv_line in enumerate(cv_lines):
        cv_text = cv_line.get('text', '')
        cv_clean = normalize_text(cv_text).replace('\n', '').replace(' ', '').replace('　', '')

        best_chunk_idx = -1
        best_overlap = 0.0
        best_missing = []

        # 각 청크와 비교
        for chunk_idx, chunk in enumerate(sentence_chunks):
            chunk_clean = normalize_text(chunk).replace('\n', '').replace(' ', '').replace('　', '')

            overlap = calculate_chunk_overlap(chunk, cv_text)

            # 이 청크가 CV 텍스트보다 긴 경우 = 누락된 글자가 있다는 뜻
            missing_chars = []
            if chunk_clean and cv_clean in chunk_clean:
                # CV가 일부만 인식함 → 누락된 글자 추출
                cv_idx = chunk_clean.find(cv_clean)
                missing_before = chunk_clean[:cv_idx]
                missing_after = chunk_clean[cv_idx + len(cv_clean):]
                missing_chars = list(missing_before + missing_after)

            # 더 높은 점수면 업데이트
            if overlap > best_overlap:
                best_overlap = overlap
                best_chunk_idx = chunk_idx
                best_missing = missing_chars

        # 점수 기준 (0.5 이상): 매칭 성공
        if best_overlap >= 0.5:
            line_matches.append({
                'cv_line_idx': cv_line_idx,
                'chunk_idx': best_chunk_idx,
                'overlap': round(best_overlap, 2),
                'missing_chars': best_missing
            })
            missing_chars_by_line[f"cv_line_{cv_line_idx}"] = best_missing

    # 매칭 타입 판단
    matched = len(line_matches) > 0
    if matched:
        if len(line_matches) == len(cv_lines) and all(lm['overlap'] >= 0.9 for lm in line_matches):
            match_type = 'perfect'
        else:
            match_type = 'partial'
    else:
        match_type = 'none'

    return {
        'matched': matched,
        'line_matches': line_matches,
        'missing_chars_by_line': missing_chars_by_line,
        'match_type': match_type
    }


def expand_bbox_for_line(
    cv_line_bbox: List[int],
    chunk_text: str,
    cv_line_text: str,
    orientation: str = 'vertical'
) -> List[int]:
    """
    개별 라인의 bbox를 확장 (청크 vs CV 라인 텍스트 길이 기반)

    케이스:
    1. chunk == cv_line: 확장 없음
    2. chunk > cv_line: width 확장 필요
    3. chunk < cv_line: CV 오류 (확장 없음)

    Args:
        cv_line_bbox: [x, y, w, h] CV의 원본 bbox
        chunk_text: Sentence의 청크 텍스트
        cv_line_text: CV Line의 텍스트
        orientation: 'vertical' | 'horizontal'

    Returns:
        확장된 [x, y, w, h]
    """
    chunk_clean = normalize_text(chunk_text).replace(' ', '').replace('　', '')
    cv_clean = normalize_text(cv_line_text).replace(' ', '').replace('　', '')

    x, y, w, h = cv_line_bbox

    # 길이 비교
    if len(chunk_clean) <= len(cv_clean):
        # 청크가 CV보다 짧거나 같음 → 확장 없음
        return [x, y, w, h]

    # 청크가 CV보다 긴 경우: 추가 글자 있음
    additional_chars = len(chunk_clean) - len(cv_clean)

    if orientation == 'vertical':
        # 세로쓰기: width 확장 (오른쪽으로)
        # 추가 글자 수 * 평균 글자 너비 (CV 기준)
        avg_char_width = w / len(cv_clean) if cv_clean else w
        expansion = int(additional_chars * avg_char_width * 1.1)  # 1.1: 여유도
        new_w = w + expansion

        return [x, y, new_w, h]
    else:
        # 가로쓰기: height 확장 (아래로)
        avg_char_height = h / len(cv_clean) if cv_clean else h
        expansion = int(additional_chars * avg_char_height * 1.1)
        new_h = h + expansion

        return [x, y, w, new_h]


def calculate_union_rect(expanded_bboxes: List[List[int]]) -> List[int]:
    """
    확장된 여러 line bbox들의 합집합 bounding_rect 계산

    Args:
        expanded_bboxes: [[x, y, w, h], ...] 확장된 line bbox들

    Returns:
        [x, y, w, h] bounding_rect
    """
    if not expanded_bboxes:
        return [0, 0, 0, 0]

    # 모든 bbox의 최소/최대 좌표 찾기
    all_x_min = []
    all_x_max = []
    all_y_min = []
    all_y_max = []

    for x, y, w, h in expanded_bboxes:
        all_x_min.append(x)
        all_x_max.append(x + w)
        all_y_min.append(y)
        all_y_max.append(y + h)

    # Union bounding rect
    x_min = min(all_x_min)
    x_max = max(all_x_max)
    y_min = min(all_y_min)
    y_max = max(all_y_max)

    # [x, y, w, h] 형식
    return [
        x_min,
        y_min,
        x_max - x_min,  # width
        y_max - y_min   # height
    ]


def detect_location_from_background(image_path: str, bounding_rect: List[int], threshold: float = 0.6) -> str:
    """
    Bounding rect 영역의 히스토그램으로 location 판단

    말풍선 안: 흰색 배경 + 검은색 글자 → White/Black 픽셀이 많음
    말풍선 밖: 회색/컬러 배경 → 중간 밝기 픽셀이 많음

    Args:
        image_path: 원본 이미지 경로
        bounding_rect: [x, y, w, h]
        threshold: Text ratio 임계값 (기본: 0.6 = 60%)

    Returns:
        "in_bubble" (Text ratio > threshold) 또는 "outside_bubble"
    """
    try:
        img = cv2.imread(image_path)
        if img is None:
            return "outside_bubble"  # 기본값

        # Bounding rect에서 좌표 추출
        x, y, w, h = bounding_rect
        x_min, y_min = int(x), int(y)
        x_max, y_max = int(x + w), int(y + h)

        # 이미지 범위 내로 제한
        img_height, img_width = img.shape[:2]
        x_min = max(0, x_min)
        y_min = max(0, y_min)
        x_max = min(img_width, x_max)
        y_max = min(img_height, y_max)

        # ROI 추출
        roi = img[y_min:y_max, x_min:x_max]

        if roi.size == 0:
            return "outside_bubble"

        # Grayscale 변환
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)

        # 히스토그램 계산
        hist = cv2.calcHist([gray], [0], None, [256], [0, 256])
        total_pixels = gray.size

        # Black (0-50), White (200-255), Gray (51-199)
        black_pixels = np.sum(hist[0:51])
        white_pixels = np.sum(hist[200:256])

        # Text ratio = (White + Black) / Total
        text_ratio = (white_pixels + black_pixels) / total_pixels

        # Text ratio > threshold → in_bubble (말풍선 안)
        return "in_bubble" if text_ratio > threshold else "outside_bubble"

    except Exception as e:
        print(f"  ⚠️ Location 판단 실패: {e}")
        return "outside_bubble"  # 실패 시 기본값


def integrate_ocr_v3(cv_clusters_path: str, gemini_ocr_path: str, output_path: str, image_path: str = None):
    """
    Sentence 중심의 OCR 통합 V3 (청크 기반 라인 매칭)

    Args:
        cv_clusters_path: cv_clusters_v5.json 경로
        gemini_ocr_path: ocr_v5.json (Gemini OCR 결과, sentence_id 사용)
        output_path: integrated.json 출력 경로
        image_path: 원본 이미지 경로 (location 판단용, 선택사항)
    """
    print(f"\n{'='*60}")
    print(f"🔄 OCR 결과 통합 V3 (청크 기반 라인 매칭)")
    print(f"{'='*60}\n")

    # CV Clusters 로드
    with open(cv_clusters_path, 'r', encoding='utf-8') as f:
        cv_clusters = json.load(f)

    # Gemini OCR 로드
    with open(gemini_ocr_path, 'r', encoding='utf-8') as f:
        gemini_result = json.load(f)

    sentences = gemini_result.get('sentences', [])

    print(f"📦 CV Clusters: {len(cv_clusters)}개")
    print(f"🤖 Gemini Sentences: {len(sentences)}개\n")

    # 통합 결과
    integrated = []

    for sentence in sentences:
        sentence_id = sentence['sentence_id']
        text = sentence.get('text', '')
        text_type = sentence.get('text_type', 'other')

        # 청크 분리
        chunks = split_sentence_into_chunks(text)

        print(f"📝 Sentence {sentence_id}: {chunks}")

        # 모든 CV Cluster에 대해 매칭 시도
        best_cluster_match = None
        best_match_score = 0.0

        for cv_cluster in cv_clusters:
            cluster_id = cv_cluster['cluster_id']
            cv_lines = cv_cluster.get('lines', [])

            # 청크를 CV 라인과 매칭
            match_result = match_chunks_to_cluster_lines(chunks, cv_lines)

            # 매칭 점수 계산
            if match_result['matched']:
                avg_overlap = np.mean([lm['overlap'] for lm in match_result['line_matches']])
                coverage = len(match_result['line_matches']) / len(cv_lines) if cv_lines else 0

                # 점수: overlap * coverage
                match_score = avg_overlap * coverage

                if match_score > best_match_score:
                    best_match_score = match_score
                    best_cluster_match = {
                        'cluster_id': cluster_id,
                        'match_result': match_result,
                        'match_score': match_score
                    }

        # 매칭 결과 처리
        if best_cluster_match and best_match_score >= 0.5:
            cluster_id = best_cluster_match['cluster_id']
            match_result = best_cluster_match['match_result']
            match_score = best_cluster_match['match_score']

            print(f"  ✅ Matched to Cluster {cluster_id} (score: {match_score:.2f})")

            # 해당 cluster 가져오기
            cluster = next(c for c in cv_clusters if c['cluster_id'] == cluster_id)
            cv_lines = cluster.get('lines', [])
            orientation = cluster.get('orientation', 'vertical')

            # ✅ cluster의 bounding_rect를 직접 사용 (이미 0.6 확장이 적용됨)
            cluster_bounding_rect = cluster.get('bounding_rect', [0, 0, 0, 0])

            # 라인별 상세 정보 수집 (디버깅/추적용)
            line_match_details = []

            for line_match in match_result['line_matches']:
                cv_line_idx = line_match['cv_line_idx']
                chunk_idx = line_match['chunk_idx']
                missing_chars = line_match['missing_chars']

                cv_line = cv_lines[cv_line_idx]
                cv_text = cv_line['text']
                cv_bbox = cv_line['bbox']
                chunk_text = chunks[chunk_idx]

                line_match_details.append({
                    'cv_line_idx': cv_line_idx,
                    'cv_text': cv_text,
                    'chunk_idx': chunk_idx,
                    'chunk_text': chunk_text,
                    'missing_chars': missing_chars,
                    'cv_bbox': cv_bbox  # 원본 CV bbox 기록
                })

            # ✅ cluster의 bounding_rect를 최종 bbox로 사용
            bounding_rect = cluster_bounding_rect

            # Location 판단
            if image_path and bounding_rect != [0, 0, 0, 0]:
                location = detect_location_from_background(image_path, bounding_rect)
            else:
                location = 'unknown'

            # 통합 결과 생성
            integrated_item = {
                'sentence_id': sentence_id,
                'text': text,
                'chunks': chunks,
                'matched_cluster': cluster_id,
                'bounding_rect': bounding_rect,
                'match_score': round(match_score, 2),
                'match_type': match_result['match_type'],
                'text_type': text_type,
                'location': location,
                'orientation': orientation,
                'line_details': line_match_details,
                'missing_chars_summary': match_result['missing_chars_by_line']
            }

        else:
            # 매칭 실패
            print(f"  ❌ No match found")

            integrated_item = {
                'sentence_id': sentence_id,
                'text': text,
                'chunks': chunks,
                'matched_cluster': None,
                'bounding_rect': [0, 0, 0, 0],
                'match_score': 0.0,
                'match_type': 'none',
                'text_type': text_type,
                'location': 'unknown',
                'orientation': 'unknown',
                'line_details': [],
                'missing_chars_summary': {}
            }

        integrated.append(integrated_item)

    # 저장
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(integrated, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 통합 완료: {len(integrated)}개 sentence")
    print(f"💾 저장: {output_path}")
    print(f"\n{'='*60}\n")


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: python integrate_ocr_v2.py <cv_clusters_v5.json> <ocr_v5.json> <output.json> [image.jpg]")
        print("\nExample:")
        print("  python integrate_ocr_v2.py temp_outputs/00029_cv_clusters_v5.json temp_outputs/00029_ocr_v5.json temp_outputs/00029_integrated_v3.json temp_outputs/00029.jpg")
        print("\nNote: image.jpg is optional for location detection (in_bubble/outside_bubble)")
        sys.exit(1)

    cv_clusters = sys.argv[1]
    gemini_ocr = sys.argv[2]
    output = sys.argv[3]
    image_path = sys.argv[4] if len(sys.argv) > 4 else None

    integrate_ocr_v3(cv_clusters, gemini_ocr, output, image_path)
