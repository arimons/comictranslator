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
import re
import os
from typing import List, Dict
from pathlib import Path
from dotenv import load_dotenv
from itertools import combinations

load_dotenv()


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


def normalize_similar_chars_for_matching(text: str) -> str:
    """
    Cluster Matching 시에만 사용 - CV 오인식 가능 문자 정규화

    유사 문자 정규화 (글자 매칭 유사도 계산용)
    - 촉음(っ/つ): つ, ツ, ッ → っ로 통일
    - 기타 유사: ん 등

    주의: 저장되는 텍스트에 적용하지 말 것 (원본 보존)
          matching 시 유사도 계산에서만 사용
    """
    if not text:
        return text

    # 촉음 정규화 (つ → っ로 통일)
    text = text.replace('つ', 'っ')
    text = text.replace('ツ', 'っ')

    return text


def split_sentence_into_chunks(text: str) -> List[str]:
    """
    Sentence를 개행 + 공백 기준으로 청크(chunk)로 분리

    Gemini OCR이 반환하는 텍스트는:
    - 개행으로 분리된 경우: "8글자\n8글자"
    - 공백으로 분리된 경우: "8글자 8글자"

    CV Cluster는 개별 라인으로 분리되어 있으므로,
    Sentence도 동일하게 분리해야 매칭 가능

    Args:
        text: "8글자\n8글자" 또는 "8글자 8글자" 형태의 텍스트

    Returns:
        ["8글자", "8글자"] 형태의 청크 리스트
    """
    # 개행으로 먼저 분리
    by_newline = text.split('\n')
    chunks = []

    for part in by_newline:
        # 각 부분을 공백(일반 공백 및 전각 공백)으로 분리
        # 공백으로 분리된 각 부분도 청크가 됨
        sub_chunks = part.split(' ')
        for chunk in sub_chunks:
            chunk = chunk.strip()
            if chunk:
                chunks.append(chunk)

    return chunks


def lcs_length(s1: str, s2: str, for_matching: bool = False) -> int:
    """
    Longest Common Subsequence (LCS) 길이 계산

    두 문자열에서 순서를 유지한 공통 부분 수열의 길이를 구함
    동적 계획법(DP) 사용

    Args:
        s1, s2: 비교할 문자열
        for_matching: True이면 유사 문자 정규화 적용 (matching 시 사용)
    """
    # Cluster matching 시에만 유사 문자 정규화 적용
    if for_matching:
        s1 = normalize_similar_chars_for_matching(s1)
        s2 = normalize_similar_chars_for_matching(s2)

    m, n = len(s1), len(s2)

    # DP 테이블
    dp = [[0] * (n + 1) for _ in range(m + 1)]

    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if s1[i-1] == s2[j-1]:
                dp[i][j] = dp[i-1][j-1] + 1
            else:
                dp[i][j] = max(dp[i-1][j], dp[i][j-1])

    return dp[m][n]


def lcs_similarity(s1: str, s2: str, for_matching: bool = False) -> float:
    """
    LCS 기반 유사도 (0.0 ~ 1.0)

    LCS 길이 / max(len(s1), len(s2))

    Args:
        s1, s2: 비교할 문자열
        for_matching: True이면 유사 문자 정규화 적용 (matching 시 사용)
    """
    if not s1 or not s2:
        return 1.0 if s1 == s2 else 0.0

    lcs_len = lcs_length(s1, s2, for_matching=for_matching)
    max_len = max(len(s1), len(s2))

    return lcs_len / max_len


def levenshtein_distance(s1: str, s2: str) -> int:
    """
    Levenshtein Distance (편집 거리) 계산

    한 문자열을 다른 문자열로 변환하는데 필요한 최소 조작 수
    - Insert (삽입) / Delete (삭제) / Replace (대체)
    """
    m, n = len(s1), len(s2)

    dp = [[0] * (n + 1) for _ in range(m + 1)]

    for i in range(m + 1):
        dp[i][0] = i
    for j in range(n + 1):
        dp[0][j] = j

    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if s1[i-1] == s2[j-1]:
                dp[i][j] = dp[i-1][j-1]
            else:
                dp[i][j] = 1 + min(
                    dp[i-1][j],      # Delete
                    dp[i][j-1],      # Insert
                    dp[i-1][j-1]     # Replace
                )

    return dp[m][n]


def levenshtein_similarity(s1: str, s2: str) -> float:
    """유사도 = 1 - (distance / max(len(s1), len(s2)))"""
    if not s1 or not s2:
        return 1.0 if s1 == s2 else 0.0
    if s1 == s2:
        return 1.0
    distance = levenshtein_distance(s1, s2)
    max_len = max(len(s1), len(s2))
    return 1.0 - (distance / max_len)


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




def expand_union_bbox(gemini_text: str, cv_text: str, cv_bbox: List[int],
                      orientation: str, cv_line_count: int) -> List[int]:
    """
    CV 누락 텍스트 보정을 위한 union_bbox 확장

    Args:
        gemini_text: Gemini OCR 문장 (띄어쓰기 포함)
        cv_text: CV 클러스터 텍스트 (모든 라인 합침)
        cv_bbox: CV 클러스터 bounding_rect [x, y, w, h]
        orientation: 'vertical' | 'horizontal'
        cv_line_count: CV 클러스터의 라인 수

    Returns:
        확장된 [x, y, w, h]
    """
    # 텍스트 정규화 (공백 제거)
    cv_clean = normalize_text(cv_text).replace(' ', '').replace('　', '')
    gemini_clean = normalize_text(gemini_text).replace(' ', '').replace('　', '')

    x, y, w, h = cv_bbox

    # 텍스트 길이 비교
    if len(gemini_clean) <= len(cv_clean):
        return cv_bbox  # 확장 불필요

    # 누락 위치 판단
    if gemini_clean.endswith(cv_clean):
        missing_position = 'prefix'  # 앞부분(오른쪽) 누락
    elif gemini_clean.startswith(cv_clean):
        missing_position = 'suffix'  # 뒷부분(왼쪽) 누락
    else:
        return cv_bbox  # 매칭 불일치

    # Gemini 라인 수 계산 (띄어쓰기로 분리)
    gemini_lines = [line.strip() for line in gemini_text.split() if line.strip()]
    gemini_line_count = len(gemini_lines)

    if orientation == 'vertical':
        # 세로쓰기: 라인 수 차이 → width 확장
        if gemini_line_count > cv_line_count and cv_line_count > 0:
            # avg_line_width 계산 (1줄은 후리가나 확장 보정)
            if cv_line_count == 1:
                avg_line_width = w * 0.7
            else:
                avg_line_width = w / cv_line_count

            additional_lines = gemini_line_count - cv_line_count
            expansion_w = int(additional_lines * avg_line_width)

            if missing_position == 'suffix':  # 왼쪽 확장
                new_x = x - expansion_w
                new_w = w + expansion_w
                return [new_x, y, new_w, h]
            else:  # prefix: 오른쪽 확장
                new_w = w + expansion_w
                return [x, y, new_w, h]

        # 글자 수 차이 → height 확장 (같은 라인 수)
        elif gemini_line_count == cv_line_count and len(gemini_clean) > len(cv_clean):
            additional_chars = len(gemini_clean) - len(cv_clean)
            avg_char_height = h / len(cv_clean) if cv_clean else h
            expansion_h = int(additional_chars * avg_char_height * 1.1)

            if missing_position == 'prefix':  # 위쪽 확장
                new_y = y - expansion_h
                new_h = h + expansion_h
                return [x, new_y, w, new_h]
            else:  # suffix: 아래쪽 확장
                new_h = h + expansion_h
                return [x, y, w, new_h]

    else:  # horizontal
        # 가로쓰기: 라인 수 차이 → height 확장
        if gemini_line_count > cv_line_count and cv_line_count > 0:
            if cv_line_count == 1:
                avg_line_height = h * 0.7
            else:
                avg_line_height = h / cv_line_count

            additional_lines = gemini_line_count - cv_line_count
            expansion_h = int(additional_lines * avg_line_height)

            if missing_position == 'prefix':  # 위쪽 확장
                new_y = y - expansion_h
                new_h = h + expansion_h
                return [x, new_y, w, new_h]
            else:  # suffix: 아래쪽 확장
                new_h = h + expansion_h
                return [x, y, w, new_h]

        # 글자 수 차이 → width 확장
        elif gemini_line_count == cv_line_count and len(gemini_clean) > len(cv_clean):
            additional_chars = len(gemini_clean) - len(cv_clean)
            avg_char_width = w / len(cv_clean) if cv_clean else w
            expansion_w = int(additional_chars * avg_char_width * 1.1)

            if missing_position == 'prefix':  # 왼쪽 확장
                new_x = x - expansion_w
                new_w = w + expansion_w
                return [new_x, y, new_w, h]
            else:  # suffix: 오른쪽 확장
                new_w = w + expansion_w
                return [x, y, new_w, h]

    return cv_bbox


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


def translate_with_gemini(texts: List[Dict], target_language: str = 'Korean') -> Dict[int, str]:
    """
    Gemini API를 사용해서 텍스트 번역

    Args:
        texts: [{'sentence_id': 1, 'text': '...'}, ...] 리스트
        target_language: 번역 대상 언어 (기본값: Korean)

    Returns:
        {sentence_id: translated_text} 딕셔너리
    """
    try:
        from gemini_api import GeminiAPI

        api_key = os.getenv('GEMINI_API_KEY')
        if not api_key:
            print("  ⚠️ GEMINI_API_KEY 미설정 - 번역 건너뛰기")
            return {}

        api = GeminiAPI(api_key)

        # JSON 리스트 구성
        texts_list = [{'id': t['sentence_id'], 'text': t['text']} for t in texts]
        texts_json = json.dumps(texts_list, ensure_ascii=False)

        prompt = f"""다음 일본어 만화 텍스트들을 자연스러운 {target_language}로 번역해주세요.

**중요 지침:**
1. 자연스럽고 적절한 어투로 번역하세요
2. 문맥을 파악하여 어투를 사용하세요
3. 효과음은 의성어/의태어로 변환하세요
4. 기술명이나 주문은 의미를 살려 번역하세요

**텍스트 목록:**
{texts_json}

**반환 형식 (JSON):**
{{"translations": [{{"sentence_id": 1, "translated": "번역된 텍스트"}}, ...]}}

순수 JSON만 반환하세요."""

        print(f"  🤖 Gemini 번역 처리 중... ({len(texts)}개)")
        response = api.translation_model.generate_content(prompt)
        response_text = response.text.strip()

        # JSON 추출
        if '```' in response_text:
            lines = response_text.split('\n')
            json_lines = []
            in_json = False
            for line in lines:
                if line.strip().startswith('```'):
                    if in_json:
                        break
                    in_json = True
                    continue
                if in_json:
                    json_lines.append(line)
            response_text = '\n'.join(json_lines)

        result = json.loads(response_text)
        translations = result.get('translations', [])

        translation_map = {}
        for t in translations:
            try:
                sid = t.get('sentence_id') if isinstance(t, dict) else t[0]
                translated = t.get('translated') if isinstance(t, dict) else t[1]
                translation_map[int(sid)] = str(translated)
            except (ValueError, IndexError, TypeError, KeyError) as e:
                print(f"    ⚠️ 항목 파싱 실패: {t}")
                continue

        print(f"  ✅ 번역 완료: {len(translation_map)}개")
        return translation_map

    except json.JSONDecodeError as e:
        print(f"  ⚠️ JSON 파싱 실패: {e}")
        print(f"     응답: {response_text[:200]}")
        return {}
    except Exception as e:
        print(f"  ⚠️ 번역 실패: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return {}


def format_json_custom(obj, indent=0):
    """
    커스텀 JSON 포맷팅:
    - 좌표 배열 (숫자만, 길이 4): [x, y, w, h]
    - 나머지는 indent 적용
    """
    indent_str = "  " * indent
    next_indent_str = "  " * (indent + 1)

    if isinstance(obj, dict):
        if not obj:
            return "{}"
        items = []
        for key, value in obj.items():
            formatted_value = format_json_custom(value, indent + 1)
            items.append(f'{next_indent_str}"{key}": {formatted_value}')
        return "{\n" + ",\n".join(items) + "\n" + indent_str + "}"

    elif isinstance(obj, list):
        if not obj:
            return "[]"

        # 모든 원소가 숫자인지 확인
        is_all_numbers = all(isinstance(x, (int, float)) for x in obj)

        if is_all_numbers:
            # 모든 숫자 배열: 한 줄로 [1, 2, 3] 또는 [x, y, w, h]
            return "[" + ", ".join(str(x) for x in obj) + "]"
        else:
            # 객체를 포함하는 배열: 들여쓰기 적용
            items = []
            for item in obj:
                formatted_item = format_json_custom(item, indent + 1)
                items.append(next_indent_str + formatted_item)
            return "[\n" + ",\n".join(items) + "\n" + indent_str + "]"

    elif isinstance(obj, str):
        return json.dumps(obj, ensure_ascii=False)
    else:
        return json.dumps(obj)


def integrate_ocr_v3(cv_clusters_path: str, gemini_ocr_path: str, output_path: str, image_path: str = None, translate: bool = True):
    """
    Sentence 중심의 OCR 통합 V3 (청크 기반 라인 매칭)

    Args:
        cv_clusters_path: cv_clusters_v5.json 경로
        gemini_ocr_path: ocr_v5.json (Gemini OCR 결과, sentence_id 사용)
        output_path: integrated.json 출력 경로
        image_path: 원본 이미지 경로 (location 판단용, 선택사항)
        translate: 번역 여부 (기본값: True)
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

    # 매칭된 Cluster 추적 (Step 1-C에서 사용)
    matched_cluster_ids_map = {}  # cluster_id -> sentence_id

    for sentence in sentences:
        sentence_id = sentence['sentence_id']
        text = sentence.get('text', '')
        text_type = sentence.get('text_type', 'other')

        # 청크 분리
        chunks = split_sentence_into_chunks(text)
        gemini_full_text = text.replace('\n', '').replace(' ', '').replace('　', '')

        print(f"📝 Sentence {sentence_id}: {chunks}")

        # ===== Step 1: 모든 가능한 N:1 Cluster 조합 → Substring 검색 =====
        print(f"  🔍 Step 1: Substring 기반 N:1 매칭 시도 중...")

        best_cluster_match = None
        best_match_score = 0.0
        best_combo_size = 0

        # 큰 조합부터 시도 (최대 3개까지)
        for combo_size in range(min(3, len(cv_clusters)), 0, -1):
            for combo_indices in combinations(range(len(cv_clusters)), combo_size):
                combo_clusters = [cv_clusters[i] for i in combo_indices]
                combo_ids = [c['cluster_id'] for c in combo_clusters]

                # 조합 텍스트 생성
                combo_text_parts = []
                for cluster in combo_clusters:
                    lines = cluster.get('lines', [])
                    combo_text_parts.append(''.join([line['text'] for line in lines]))
                combo_text_raw = ''.join(combo_text_parts).replace('\n', '').replace(' ', '').replace('　', '')

                # 정규화 적용 (말줄임표, 느낌표 등)
                combo_text = normalize_text(combo_text_raw)
                gemini_normalized = normalize_text(gemini_full_text)

                # Sentence에 substring으로 포함되는지 확인
                if combo_text in gemini_normalized:
                    combo_score = 1.0
                    if combo_size <= 2:
                        print(f"    C{'+'.join(map(str, combo_ids))}: substring=1.00 ✅")

                    # 더 큰 조합이 있으면 계속, 같은 크기면 첫 번째 사용
                    if combo_size > best_combo_size:
                        best_combo_size = combo_size
                        best_match_score = combo_score
                        best_cluster_match = {
                            'cluster_id': combo_ids[0],
                            'match_result': None,
                            'match_score': combo_score,
                            'lcs_score': combo_score,
                            'cluster_combination': combo_ids
                        }

            # substring 매칭 찾으면 더 작은 조합은 시도하지 않음
            if best_cluster_match:
                break

        # Substring 매칭 없으면 LCS/Levenshtein으로 재매칭
        if not best_cluster_match:
            print(f"  🔄 Step 1-B: LCS/Levenshtein 매칭 (Cluster 기준)...")
            for cv_cluster in cv_clusters:
                cluster_id = cv_cluster['cluster_id']
                cv_lines = cv_cluster.get('lines', [])
                cv_full_text_raw = ''.join([line['text'] for line in cv_lines]).replace('\n', '').replace(' ', '').replace('　', '')

                # 정규화 적용
                cv_full_text = normalize_text(cv_full_text_raw)
                gemini_normalized = normalize_text(gemini_full_text)

                # ✅ 부분 매칭 로직: Cluster 기준 점수 사용
                lcs_len = lcs_length(gemini_normalized, cv_full_text)
                lev_dist = levenshtein_distance(gemini_normalized, cv_full_text)

                # Cluster 기준: Cluster의 몇 %가 Sentence와 매칭되는가?
                cluster_lcs_ratio = lcs_len / len(cv_full_text) if cv_full_text else 0.0
                cluster_lev_score = 1 - (lev_dist / len(cv_full_text)) if cv_full_text else 0.0

                # 표준 방식: Sentence 기준
                # ✨ for_matching=True: CV 오인식 문자(つ→っ) 정규화 적용
                lcs_score = lcs_similarity(gemini_normalized, cv_full_text, for_matching=True)
                lev_score = levenshtein_similarity(gemini_normalized, cv_full_text)

                # ✅ 최종 점수: Cluster 기준 점수로 계산 (부분 매칭 허용)
                match_score = max(cluster_lcs_ratio, cluster_lev_score)

                print(f"    C{cluster_id}: cluster_lcs={cluster_lcs_ratio:.2f}, cluster_lev={cluster_lev_score:.2f}, match={match_score:.2f}")

                if match_score > best_match_score:
                    best_match_score = match_score
                    best_cluster_match = {
                        'cluster_id': cluster_id,
                        'match_result': None,
                        'match_score': match_score,
                        'lcs_score': match_score,
                        'cluster_combination': [cluster_id]
                    }

        # 매칭 결과 처리
        if best_cluster_match and best_match_score >= 0.8:
            cluster_id = best_cluster_match['cluster_id']
            cluster_combination = best_cluster_match['cluster_combination']  # ✅ N:1 조합 사용
            match_result = best_cluster_match['match_result']
            match_score = best_cluster_match['match_score']

            print(f"  ✅ Matched to Cluster {cluster_id} (score: {match_score:.2f})")

            # ✅ 복수 Cluster인 경우 Union bbox 계산
            if len(cluster_combination) > 1:
                # Union bbox 계산
                bboxes = []
                for cid in cluster_combination:
                    c = next((c for c in cv_clusters if c['cluster_id'] == cid), None)
                    if c:
                        bbox = c.get('bounding_rect', [0, 0, 0, 0])
                        if bbox != [0, 0, 0, 0]:
                            bboxes.append(bbox)

                if bboxes:
                    x_min = min(b[0] for b in bboxes)
                    y_min = min(b[1] for b in bboxes)
                    x_max = max(b[0] + b[2] for b in bboxes)
                    y_max = max(b[1] + b[3] for b in bboxes)
                    cluster_bounding_rect = [x_min, y_min, x_max - x_min, y_max - y_min]
                    print(f"    📦 Union bbox (복수 Cluster): {cluster_bounding_rect}")
                else:
                    cluster_bounding_rect = [0, 0, 0, 0]
            else:
                # 단일 Cluster: 기존 로직
                cluster = next(c for c in cv_clusters if c['cluster_id'] == cluster_id)
                cluster_bounding_rect = cluster.get('bounding_rect', [0, 0, 0, 0])

            # 해당 cluster 가져오기 (orientation 등 정보 추출용)
            cluster = next(c for c in cv_clusters if c['cluster_id'] == cluster_id)
            cv_lines = cluster.get('lines', [])
            orientation = cluster.get('orientation', 'vertical')

            # 라인별 상세 정보 수집 (디버깅/추적용)
            line_match_details = []

            if match_result and match_result.get('line_matches'):
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

            # 통합 결과 생성 - cluster 텍스트 추가
            cluster_text = ''.join([line['text'] for line in cv_lines])

            # CV 누락 텍스트 보정 (텍스트 길이 차이 기준)
            cv_clean = normalize_text(cluster_text).replace(' ', '').replace('　', '')
            gemini_clean = normalize_text(text).replace(' ', '').replace('　', '')

            if len(gemini_clean) > len(cv_clean):
                print(f"  🔧 Expanding union_bbox (Gemini {len(gemini_clean)} > CV {len(cv_clean)})")
                print(f"     Gemini: '{text}'")
                print(f"     CV: '{cluster_text}'")
                print(f"     Before: {cluster_bounding_rect}")
                expanded = expand_union_bbox(
                    gemini_text=text,
                    cv_text=cluster_text,
                    cv_bbox=cluster_bounding_rect,
                    orientation=orientation,
                    cv_line_count=len(cv_lines)
                )
                print(f"     After: {expanded}")
                bounding_rect = expanded

            # Location 판단
            if image_path and bounding_rect != [0, 0, 0, 0]:
                location = detect_location_from_background(image_path, bounding_rect)
            else:
                location = 'unknown'

            # matched_cluster_details 구성
            matched_cluster_details = []
            cluster_ids = cluster_combination  # ✅ N:1 조합 사용

            for cid in cluster_ids:
                cv_cluster = next((c for c in cv_clusters if c['cluster_id'] == cid), None)
                if cv_cluster:
                    matched_cluster_details.append({
                        'cluster_id': cid,
                        'orientation': cv_cluster.get('orientation', 'unknown'),
                        'lines': cv_cluster.get('lines', []),
                        'bounding_rect': cv_cluster.get('bounding_rect', [0, 0, 0, 0])
                    })

            integrated_item = {
                'sentence_id': sentence_id,
                'text': text,
                'matched_cluster': cluster_combination,  # ✅ N:1 조합 사용
                'matched_cluster_details': matched_cluster_details,
                'union_bbox': bounding_rect,  # ✅ Union bbox만 사용
                'text_type': text_type,
                'location': location,
                'orientation': orientation,
                'match_score': match_score,
                'text_translated': ''
            }

        else:
            # Step 1-C: Line별 일치 확인 (unmatched clusters만, 1개라도 일치하면 매칭)
            print(f"  🔍 Step 1-C: Line별 일치 확인 (unmatched clusters)...")

            # 이미 매칭된 clusters 제외
            unmatched_clusters = [c for c in cv_clusters if c['cluster_id'] not in matched_cluster_ids_map]

            line_matched_cluster = None
            for cv_cluster in unmatched_clusters:
                cluster_id = cv_cluster['cluster_id']
                cv_lines = cv_cluster.get('lines', [])

                # 각 CV line이 sentence chunks와 일치하는지 확인
                matched_lines = 0
                for cv_line in cv_lines:
                    cv_line_text = normalize_text(cv_line['text']).replace(' ', '').replace('　', '')

                    # 각 chunk와 비교
                    for chunk in chunks:
                        chunk_text = normalize_text(chunk).replace(' ', '').replace('　', '')

                        # 완벽 일치 또는 포함 관계 확인
                        if cv_line_text == chunk_text or cv_line_text in chunk_text or chunk_text in cv_line_text:
                            matched_lines += 1
                            break

                # 1개라도 매칭되면 즉시 채택 (점수 무관)
                if matched_lines >= 1:
                    print(f"    C{cluster_id}: line_match={matched_lines}/{len(cv_lines)} ✅ (즉시 매칭)")
                    line_matched_cluster = cv_cluster
                    break
                else:
                    print(f"    C{cluster_id}: line_match={matched_lines}/{len(cv_lines)}")

            # Step 1-C에서 매칭 성공한 경우
            if line_matched_cluster:
                cluster_id = line_matched_cluster['cluster_id']
                cluster_bounding_rect = line_matched_cluster.get('bounding_rect', [0, 0, 0, 0])
                cv_lines = line_matched_cluster.get('lines', [])
                orientation = line_matched_cluster.get('orientation', 'vertical')

                print(f"  ✅ Matched to Cluster {cluster_id} (Line match)")

                # Location 판단
                if image_path and cluster_bounding_rect != [0, 0, 0, 0]:
                    location = detect_location_from_background(image_path, cluster_bounding_rect)
                else:
                    location = 'unknown'

                integrated_item = {
                    'sentence_id': sentence_id,
                    'text': text,
                    'matched_cluster': [cluster_id],
                    'matched_cluster_details': [{
                        'cluster_id': cluster_id,
                        'orientation': orientation,
                        'lines': cv_lines,
                        'bounding_rect': cluster_bounding_rect
                    }],
                    'union_bbox': cluster_bounding_rect,
                    'text_type': text_type,
                    'location': location,
                    'orientation': orientation,
                    'match_score': 0.8,  # Line 매칭 성공 점수
                    'text_translated': ''
                }

                # matched_cluster_ids_map에 추가
                matched_cluster_ids_map[cluster_id] = sentence_id

            else:
                # 매칭 완전 실패
                print(f"  ❌ No match found")

                integrated_item = {
                    'sentence_id': sentence_id,
                    'text': text,
                    'matched_cluster': [],
                    'matched_cluster_details': [],
                    'text_type': text_type,
                    'location': 'unknown',
                    'orientation': 'unknown',
                    'match_score': 0.0,
                    'text_translated': ''
                }

        integrated.append(integrated_item)

    # ===== Step 4: 매칭된 문장에서 추가 클러스터 검색 (N:1 매칭) =====
    print(f"\n{'='*60}")
    print(f"🔍 매칭된 문장에서 추가 클러스터 검색 (N:1 매칭)")
    print(f"{'='*60}")

    # 현재 매칭된 클러스터 ID 추가 수집 (Step 1-3에서 이미 초기화됨)
    for item in integrated:
        if item['matched_cluster'] is not None:
            mc = item.get('matched_cluster')
            if isinstance(mc, str):
                # "C1+C2" 또는 "C1" 형식에서 ID 추출
                cluster_ids_str = mc.split(' (')[0]
                for cid_str in cluster_ids_str.split('+'):
                    try:
                        cid = int(cid_str)
                        if cid not in matched_cluster_ids_map:
                            matched_cluster_ids_map[cid] = item['sentence_id']
                    except ValueError:
                        pass

    # 매칭된 각 문장에 대해 추가 클러스터 검색 (substring 기반)
    additional_matches = []  # (sentence_id, cluster_id, cluster_text) 리스트
    for matched_item in [item for item in integrated if item['matched_cluster'] is not None]:
        sentence_id = matched_item['sentence_id']
        text = matched_item['text']
        chunks = split_sentence_into_chunks(text)
        gemini_full_text = ''.join(chunks).replace('\n', '').replace(' ', '').replace('　', '')

        print(f"\n  📝 S{sentence_id}: '{text}'")
        print(f"    기존 매칭: {matched_item['matched_cluster']}")

        # ✅ 기존 매칭된 Cluster 텍스트 추출 (제거용) - 리스트/문자열 모두 지원
        existing_matched_texts = []
        existing_mc = matched_item['matched_cluster']

        if existing_mc:
            if isinstance(existing_mc, list):
                # 리스트 형식: [1, 2, 3] → 각 cluster의 텍스트 추출
                for cluster_id in existing_mc:
                    cluster = next((c for c in cv_clusters if c['cluster_id'] == cluster_id), None)
                    if cluster:
                        cv_lines = cluster.get('lines', [])
                        matched_text = ''.join([line['text'] for line in cv_lines]).replace('\n', '').replace(' ', '').replace('　', '')
                        existing_matched_texts.append(matched_text)
            elif isinstance(existing_mc, str):
                # 문자열 형식: "2 (奴らの)" 형식에서 괄호 안 텍스트 추출
                if '(' in existing_mc and ')' in existing_mc:
                    matched_text = existing_mc.split('(')[1].split(')')[0]
                    existing_matched_texts.append(matched_text)

        # 미매칭 클러스터 중에서 추가 매칭 검색 (substring + 유사도 기반)
        additional_clusters = []
        additional_cluster_candidates = []  # 추가 후보 (조합 테스트용)

        for cv_cluster in cv_clusters:
            cluster_id = cv_cluster['cluster_id']
            if cluster_id in matched_cluster_ids_map:
                continue  # 이미 매칭된 클러스터는 스킵

            cv_lines = cv_cluster.get('lines', [])
            cv_full_text = ''.join([line['text'] for line in cv_lines]).replace('\n', '').replace(' ', '').replace('　', '')

            # Substring 기반: Cluster가 Sentence에 포함되면 100점 → 즉시 추가
            if cv_full_text in gemini_full_text:
                additional_clusters.append({
                    'cluster_id': cluster_id,
                    'score': 1.0,
                    'cv_text': cv_full_text,
                    'is_substring': True
                })
                # 바로 추가 매칭으로 등록
                additional_matches.append((sentence_id, cluster_id, cv_full_text))
            else:
                # 유사도 계산: 기존 매칭 텍스트를 제거한 후 비교 (LCS + Levenshtein 중 높은 값)
                comparison_text = gemini_full_text

                # 기존 매칭 텍스트 제거
                for matched_text in existing_matched_texts:
                    matched_clean = matched_text.replace('\n', '').replace(' ', '').replace('　', '')
                    if matched_clean in comparison_text:
                        comparison_text = comparison_text.replace(matched_clean, '', 1)

                # 비교 텍스트가 너무 짧으면 원본 사용
                if not comparison_text.strip():
                    comparison_text = gemini_full_text

                # ✅ 부분 매칭 로직: Cluster 기준 점수 사용
                lcs_len = lcs_length(comparison_text, cv_full_text)
                lev_dist = levenshtein_distance(comparison_text, cv_full_text)

                cluster_lcs_ratio = lcs_len / len(cv_full_text) if cv_full_text else 0.0
                cluster_lev_score = 1 - (lev_dist / len(cv_full_text)) if cv_full_text else 0.0

                similarity_score = max(cluster_lcs_ratio, cluster_lev_score)

                # 개별 점수 >= 0.7이면 즉시 추가 (조합 재평가 불필요)
                if similarity_score >= 0.7:
                    additional_clusters.append({
                        'cluster_id': cluster_id,
                        'score': similarity_score,
                        'cv_text': cv_full_text,
                        'is_substring': False
                    })
                    # 바로 추가 매칭으로 등록
                    additional_matches.append((sentence_id, cluster_id, cv_full_text))

        if additional_clusters:
            # 점수 높은 순으로 정렬 (substring 우선)
            additional_clusters.sort(key=lambda x: (x['is_substring'], x['score']), reverse=True)
            for ac in additional_clusters:
                marker = "✅" if ac['is_substring'] else "💡"
                print(f"    {marker} 추가 확정: C{ac['cluster_id']} ({ac['cv_text']}) - 점수: {ac['score']:.2f}")
        else:
            print(f"    ℹ️ 추가 매칭 클러스터 없음")

    # 추가 매칭을 integrated에 반영 (같은 Sentence의 여러 Cluster)
    if additional_matches:
        print(f"\n  ✅ 추가 매칭 반영:")
        for sentence_id, cluster_id, cluster_text in additional_matches:
            for item in integrated:
                if item['sentence_id'] == sentence_id and item['matched_cluster']:
                    # 기존 매칭에 추가 클러스터 추가 (리스트 형식)
                    if cluster_id not in item['matched_cluster']:
                        item['matched_cluster'].append(cluster_id)

                        # matched_cluster_details에도 추가
                        cv_cluster = next((c for c in cv_clusters if c['cluster_id'] == cluster_id), None)
                        if cv_cluster:
                            item['matched_cluster_details'].append({
                                'cluster_id': cluster_id,
                                'orientation': cv_cluster.get('orientation', 'unknown'),
                                'lines': cv_cluster.get('lines', []),
                                'bounding_rect': cv_cluster.get('bounding_rect', [0, 0, 0, 0])
                            })

                        # ✅ Cluster ID를 bbox 위치 기준으로 정렬 (vertical: y 오름차순)
                        cluster_ids = item['matched_cluster']
                        if len(cluster_ids) > 1:
                            # orientation 확인 (첫 번째 클러스터 기준)
                            first_cluster = next((c for c in cv_clusters if c['cluster_id'] == cluster_ids[0]), None)
                            orientation = first_cluster.get('orientation', 'vertical') if first_cluster else 'vertical'

                            # bbox 기준 정렬 (vertical: y 오름차순, horizontal: x 오름차순)
                            def get_cluster_sort_key(cid):
                                c = next((c for c in cv_clusters if c['cluster_id'] == cid), None)
                                if c:
                                    bbox = c.get('bounding_rect', [0, 0, 0, 0])
                                    if orientation == 'vertical':
                                        return bbox[1]  # y 좌표
                                    else:
                                        return bbox[0]  # x 좌표
                                return 0

                            cluster_ids.sort(key=get_cluster_sort_key)
                            item['matched_cluster'] = cluster_ids

                        # ✅ union_bbox 재계산 (복수 Cluster인 경우)
                        if len(cluster_ids) > 1:
                            bboxes = []
                            for cid in cluster_ids:
                                c = next((c for c in cv_clusters if c['cluster_id'] == cid), None)
                                if c:
                                    bbox = c.get('bounding_rect', [0, 0, 0, 0])
                                    if bbox != [0, 0, 0, 0]:
                                        bboxes.append(bbox)

                            if bboxes:
                                x_min = min(b[0] for b in bboxes)
                                y_min = min(b[1] for b in bboxes)
                                x_max = max(b[0] + b[2] for b in bboxes)
                                y_max = max(b[1] + b[3] for b in bboxes)
                                item['union_bbox'] = [x_min, y_min, x_max - x_min, y_max - y_min]

                        existing_ids = '+'.join(str(cid) for cid in item['matched_cluster'])
                        print(f"    S{sentence_id}: {item['matched_cluster'][:-1]} → {existing_ids}")
                    break

    # ===== Step 5: 미매칭 클러스터 재매칭 =====
    print(f"\n{'='*60}")
    print(f"🔄 미매칭 클러스터 재매칭 시도")
    print(f"{'='*60}")

    # 미매칭 문장 찾기
    unmatched_items = [item for item in integrated if item['matched_cluster'] is None]

    if unmatched_items:
        print(f"📍 미매칭 문장: {len(unmatched_items)}개")

        for unmatched_item in unmatched_items:
            sentence_id = unmatched_item['sentence_id']
            text = unmatched_item['text']
            gemini_full_text = text.replace('\n', '').replace(' ', '').replace('　', '')

            print(f"\n  📝 S{sentence_id}: '{text}'")

            # 현재 매칭된 클러스터 ID 수집 (리스트 형식)
            matched_ids = set()
            for item in integrated:
                if item['matched_cluster']:  # 비어있지 않은 리스트
                    for cluster_id in item['matched_cluster']:
                        matched_ids.add(cluster_id)

            # 미매칭 클러스터만 재매칭 시도 (개별 및 조합)
            best_rematch_score = 0.0
            best_rematch = None
            unmatched_cluster_list = [c for c in cv_clusters if c['cluster_id'] not in matched_ids]

            # Step 1: 개별 클러스터 재매칭
            print(f"    🔍 개별 클러스터 재매칭 (Cluster 기준)...")
            for cv_cluster in unmatched_cluster_list:
                cluster_id = cv_cluster['cluster_id']
                cv_lines = cv_cluster.get('lines', [])
                cv_full_text = ''.join([line['text'] for line in cv_lines]).replace('\n', '').replace(' ', '').replace('　', '')

                # ✅ 부분 매칭 로직: Cluster 기준 점수 사용
                if cv_full_text in gemini_full_text:
                    # Substring 매칭: 100점
                    lcs_score = 1.0
                    print(f"      C{cluster_id}: substring=1.00 ✅")
                else:
                    # Cluster 기준: Cluster의 몇 %가 Sentence와 매칭되는가?
                    lcs_len = lcs_length(gemini_full_text, cv_full_text)
                    lev_dist = levenshtein_distance(gemini_full_text, cv_full_text)

                    cluster_lcs_ratio = lcs_len / len(cv_full_text) if cv_full_text else 0.0
                    cluster_lev_score = 1 - (lev_dist / len(cv_full_text)) if cv_full_text else 0.0

                    lcs_score = max(cluster_lcs_ratio, cluster_lev_score)
                    print(f"      C{cluster_id}: cluster_lcs={cluster_lcs_ratio:.2f}, cluster_lev={cluster_lev_score:.2f}, score={lcs_score:.2f}")

                if lcs_score > best_rematch_score:
                    best_rematch_score = lcs_score
                    best_rematch = {
                        'cluster_ids': [cluster_id],
                        'score': lcs_score
                    }

            # Step 2: 미매칭 클러스터 조합 (0.8 이상 목표)
            if best_rematch_score < 0.8 and len(unmatched_cluster_list) > 1:
                print(f"    🔗 클러스터 조합 재매칭 (Cluster 기준, 0.8 이상 목표)...")
                for start_idx in range(len(unmatched_cluster_list)):
                    for end_idx in range(start_idx + 2, min(start_idx + 4, len(unmatched_cluster_list) + 1)):
                        combo_clusters = unmatched_cluster_list[start_idx:end_idx]
                        combo_ids = [c['cluster_id'] for c in combo_clusters]

                        combo_text_parts = []
                        for cluster in combo_clusters:
                            lines = cluster.get('lines', [])
                            combo_text_parts.append(''.join([line['text'] for line in lines]))

                        combo_text = ''.join(combo_text_parts).replace('\n', '').replace(' ', '').replace('　', '')

                        # ✅ 부분 매칭 로직: Cluster 기준 점수 사용
                        if combo_text in gemini_full_text:
                            # Substring 매칭: 100점
                            combo_lcs_score = 1.0
                            if len(combo_ids) <= 2:
                                print(f"      C{'+'.join(map(str, combo_ids))}: substring=1.00 ✅")
                        else:
                            # Cluster 기준: Cluster 조합의 몇 %가 Sentence와 매칭되는가?
                            combo_lcs_len = lcs_length(gemini_full_text, combo_text)
                            combo_lev_dist = levenshtein_distance(gemini_full_text, combo_text)

                            combo_cluster_lcs_ratio = combo_lcs_len / len(combo_text) if combo_text else 0.0
                            combo_cluster_lev_score = 1 - (combo_lev_dist / len(combo_text)) if combo_text else 0.0

                            combo_lcs_score = max(combo_cluster_lcs_ratio, combo_cluster_lev_score)
                            if len(combo_ids) <= 2:
                                print(f"      C{'+'.join(map(str, combo_ids))}: cluster_lcs={combo_cluster_lcs_ratio:.2f}, cluster_lev={combo_cluster_lev_score:.2f}, score={combo_lcs_score:.2f}")

                        if combo_lcs_score > best_rematch_score:
                            best_rematch_score = combo_lcs_score
                            best_rematch = {
                                'cluster_ids': combo_ids,
                                'score': combo_lcs_score
                            }

                        # 0.8 이상이면 멈추기
                        if best_rematch_score >= 0.8:
                            print(f"      ✅ 0.8 이상 달성: {best_rematch_score:.2f}")
                            break
                    if best_rematch_score >= 0.8:
                        break

            # 재매칭 결과 처리 (0.8 이상)
            if best_rematch and best_rematch_score >= 0.8:
                cluster_ids = best_rematch['cluster_ids']

                # 조합된 클러스터의 텍스트와 bounding_rect 생성
                combo_text_parts = []
                bounding_rects = []
                for cid in cluster_ids:
                    cluster = next(c for c in cv_clusters if c['cluster_id'] == cid)
                    cv_lines = cluster.get('lines', [])
                    combo_text_parts.append(''.join([line['text'] for line in cv_lines]))
                    bounding_rects.append(cluster.get('bounding_rect', [0, 0, 0, 0]))

                cluster_text = ''.join(combo_text_parts)

                # ✅ 복수 Cluster인 경우 Union bbox 계산, 단일인 경우 첫 번째 사용
                if len(cluster_ids) > 1:
                    # Union bbox 계산
                    valid_bboxes = [b for b in bounding_rects if b != [0, 0, 0, 0]]
                    if valid_bboxes:
                        x_min = min(b[0] for b in valid_bboxes)
                        y_min = min(b[1] for b in valid_bboxes)
                        x_max = max(b[0] + b[2] for b in valid_bboxes)
                        y_max = max(b[1] + b[3] for b in valid_bboxes)
                        bounding_rect = [x_min, y_min, x_max - x_min, y_max - y_min]
                        print(f"    📦 Union bbox (복수 Cluster 재매칭): {bounding_rect}")
                    else:
                        bounding_rect = bounding_rects[0]
                else:
                    # 단일 Cluster
                    bounding_rect = bounding_rects[0]

                # 첫 번째 클러스터에서 orientation 가져오기
                first_cluster = next(c for c in cv_clusters if c['cluster_id'] == cluster_ids[0])
                orientation = first_cluster.get('orientation', 'vertical')

                # Location 판단
                if image_path and bounding_rect != [0, 0, 0, 0]:
                    location = detect_location_from_background(image_path, bounding_rect)
                else:
                    location = 'unknown'

                # 리스트 형식으로 matched_cluster 저장
                unmatched_item['matched_cluster'] = cluster_ids

                # matched_cluster_details 구성
                matched_cluster_details = []
                for cid in cluster_ids:
                    cv_cluster = next((c for c in cv_clusters if c['cluster_id'] == cid), None)
                    if cv_cluster:
                        matched_cluster_details.append({
                            'cluster_id': cid,
                            'orientation': cv_cluster.get('orientation', 'unknown'),
                            'lines': cv_cluster.get('lines', []),
                            'bounding_rect': cv_cluster.get('bounding_rect', [0, 0, 0, 0])
                        })

                unmatched_item['matched_cluster_details'] = matched_cluster_details
                unmatched_item['union_bbox'] = bounding_rect  # ✅ Union bbox만 사용
                unmatched_item['location'] = location
                unmatched_item['orientation'] = orientation
                unmatched_item['match_score'] = best_rematch_score

                print(f"    ✅ 재매칭: C{cluster_ids_str} ({cluster_text}) - 점수: {best_rematch_score:.2f}")

                # matched_cluster_ids_map 업데이트
                for cid in cluster_ids:
                    matched_cluster_ids_map[cid] = sentence_id
            else:
                print(f"    ❌ 재매칭 실패 (최고 점수: {best_rematch_score:.2f})")

    # ===== 최종 match_score 재계산 =====
    print(f"\n{'='*60}")
    print(f"🔢 최종 매칭 점수 재계산")
    print(f"{'='*60}")

    for item in integrated:
        if item['matched_cluster']:
            sentence_id = item['sentence_id']
            text = item['text']
            gemini_full_text = text.replace('\n', '').replace(' ', '').replace('　', '')

            # matched_cluster는 리스트 형식 [1, 2, 3]
            cluster_ids = item.get('matched_cluster', [])

            if cluster_ids:
                # 조합된 클러스터 텍스트
                combo_text_parts = []
                for cid in cluster_ids:
                    cluster = next((c for c in cv_clusters if c['cluster_id'] == cid), None)
                    if cluster:
                        cv_lines = cluster.get('lines', [])
                        combo_text_parts.append(''.join([line['text'] for line in cv_lines]))

                combo_text = ''.join(combo_text_parts).replace('\n', '').replace(' ', '').replace('　', '')

                # 최종 점수 계산
                # ✨ for_matching=True: CV 오인식 문자(つ→っ) 정규화 적용
                lcs_score = lcs_similarity(gemini_full_text, combo_text, for_matching=True)
                lev_score = levenshtein_similarity(gemini_full_text, combo_text)
                final_score = max(lcs_score, lev_score)

                # 기존 점수 저장 (변경 전)
                old_score = item['match_score']
                item['match_score'] = final_score

                # 다중 클러스터인 경우만 출력
                if len(cluster_ids) > 1:
                    cluster_id_str = '+'.join(map(str, cluster_ids))
                    print(f"  S{sentence_id}: C{cluster_id_str}")
                    print(f"    조합 텍스트: '{combo_text}'")
                    print(f"    이전 점수: {old_score:.2f} → 최종 점수: {final_score:.2f}")

    print(f"\n{'='*60}\n")

    # ✅ bounding_rect_expanded 필드 완전 제거
    for item in integrated:
        if 'bounding_rect_expanded' in item:
            del item['bounding_rect_expanded']

    # 번역 처리 (옵션)
    if translate and integrated:
        print(f"\n🌐 번역 처리 중...")
        texts_to_translate = [
            {'sentence_id': item['sentence_id'], 'text': item['text']}
            for item in integrated
            if item['text'].strip()
        ]

        if texts_to_translate:
            translation_map = translate_with_gemini(texts_to_translate, 'Korean')
            for item in integrated:
                item['text_translated'] = translation_map.get(item['sentence_id'], '')
            print(f"  ✅ 번역된 항목: {len(translation_map)}개 / 전체: {len(integrated)}개")
        else:
            for item in integrated:
                item['text_translated'] = ''
    else:
        for item in integrated:
            item['text_translated'] = ''

    # Unmatched clusters 계산 (✅ 필요한 필드)
    matched_cluster_ids = set()
    for item in integrated:
        mc = item.get('matched_cluster', [])
        if isinstance(mc, list):
            # 리스트 형식: [1, 2, 3]
            for cid in mc:
                matched_cluster_ids.add(cid)
        elif isinstance(mc, int):
            matched_cluster_ids.add(mc)

    # unmatched_clusters 생성
    unmatched_clusters = []
    for cv_cluster in cv_clusters:
        cluster_id = cv_cluster['cluster_id']
        if cluster_id not in matched_cluster_ids:
            cv_lines = cv_cluster.get('lines', [])
            cluster_text = ''.join([line['text'] for line in cv_lines])
            unmatched_clusters.append({
                'cluster_id': cluster_id,
                'text': cluster_text,
                'bounding_rect': cv_cluster.get('bounding_rect', [0, 0, 0, 0])
            })

    # 저장 (✅ unmatched_clusters 포함)
    save_data = {
        'clusters': integrated,
        'unmatched_clusters': unmatched_clusters
    }

    # 커스텀 포맷팅으로 저장 (좌표만 한 줄, 나머지는 들여쓰기)
    json_str = format_json_custom(save_data)

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(json_str)

    print(f"\n✅ 통합 완료: {len(integrated)}개 sentence")
    print(f"💾 저장: {output_path}")
    print(f"   필드: text, text_translated, bounding_rect, matched_cluster, match_score 등")
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
