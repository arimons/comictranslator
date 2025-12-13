#!/usr/bin/env python3
"""
CV 결과 필터링 유틸리티
- 갑옷 부대마크, 배경 패턴 등 비-텍스트 객체 제거
- 크기, 글자 수, 기호 패턴 기반 필터링
"""
import json
from typing import List, Dict
from pathlib import Path
import re


def get_cluster_size(bbox: List[int]) -> int:
    """클러스터 bbox 크기 계산 (픽셀)"""
    if not bbox or len(bbox) < 4:
        return 0
    x, y, w, h = bbox[0], bbox[1], bbox[2], bbox[3]
    return w * h


def count_characters(cluster: Dict) -> int:
    """클러스터 내 총 문자 수"""
    lines = cluster.get('lines', [])
    total = 0
    for line in lines:
        text = line.get('text', '')
        total += len(text)
    return total


def is_mostly_symbols(text: str) -> bool:
    """텍스트가 기호/특수문자 위주인지 판단"""
    if not text:
        return False

    # 한글, 한자, 히라가나, 가타가나 제거
    japanese_hiragana = r'[\u3040-\u309F]'  # 히라가나
    japanese_katakana = r'[\u30A0-\u30FF]'  # 가타가나
    japanese_kanji = r'[\u4E00-\u9FFF]'     # 한자/CJK
    korean = r'[\uAC00-\uD7AF]'              # 한글

    text_only = re.sub(japanese_hiragana + '|' + japanese_katakana + '|' + japanese_kanji + '|' + korean, '', text)

    if not text_only:
        return False

    # 남은 문자가 모두 기호/숫자인지 확인
    symbol_pattern = r'[^\w\s]'  # 기호
    symbols = len(re.findall(symbol_pattern, text_only))
    alphanumeric = len(re.findall(r'\w', text_only))

    if symbols + alphanumeric == 0:
        return True

    symbol_ratio = symbols / (symbols + alphanumeric)
    return symbol_ratio > 0.7


def should_filter_cluster(cluster: Dict, min_size: int = 50) -> bool:
    """클러스터를 필터링해야 하는지 판단"""

    bbox = cluster.get('bounding_rect', [0, 0, 0, 0])
    size = get_cluster_size(bbox)
    char_count = count_characters(cluster)
    text = ' '.join([line['text'] for line in cluster.get('lines', [])])

    # 조건 1: 너무 작은 크기 (마크, 데코레이션 가능성)
    if size < min_size:
        return True

    # 조건 2: 글자가 없음 (bbox만 있음)
    if char_count == 0:
        return True

    # 조건 3: 기호/특수문자 위주 (마크 패턴)
    if is_mostly_symbols(text):
        return True

    # 조건 4: 1글자만 있으면서 크기가 작음 (단독 기호)
    if char_count == 1 and size < 100:
        return True

    return False


def filter_cv_clusters(clusters: List[Dict], min_size: int = 50) -> tuple:
    """
    CV 클러스터 필터링

    Returns:
        (filtered_clusters, removed_count, removed_details)
    """
    filtered = []
    removed = []

    for cluster in clusters:
        if should_filter_cluster(cluster, min_size):
            removed.append({
                'cluster_id': cluster['cluster_id'],
                'text': ' '.join([line['text'] for line in cluster.get('lines', [])]),
                'bbox': cluster.get('bounding_rect', [0, 0, 0, 0]),
                'size': get_cluster_size(cluster.get('bounding_rect', [0, 0, 0, 0])),
                'reason': get_filter_reason(cluster, min_size)
            })
        else:
            filtered.append(cluster)

    return filtered, len(removed), removed


def get_filter_reason(cluster: Dict, min_size: int = 50) -> str:
    """필터링 사유 설명"""
    bbox = cluster.get('bounding_rect', [0, 0, 0, 0])
    size = get_cluster_size(bbox)
    char_count = count_characters(cluster)
    text = ' '.join([line['text'] for line in cluster.get('lines', [])])

    if size < min_size:
        return f"너무 작음 ({size} px)"
    if char_count == 0:
        return "글자 없음"
    if is_mostly_symbols(text):
        return "기호/패턴 위주"
    if char_count == 1 and size < 100:
        return "단독 기호 (1글자)"

    return "알 수 없음"


def main():
    import sys

    if len(sys.argv) < 2:
        print("Usage: python cv_result_filter.py <cv_clusters.json> [--min-size 50] [--show-removed]")
        sys.exit(1)

    input_file = sys.argv[1]
    min_size = 50
    show_removed = False

    # 옵션 파싱
    for i, arg in enumerate(sys.argv[2:]):
        if arg == '--min-size' and i + 3 < len(sys.argv):
            min_size = int(sys.argv[i + 3])
        elif arg == '--show-removed':
            show_removed = True

    # 파일 로드
    input_path = Path(input_file)
    if not input_path.exists():
        print(f"❌ 파일을 찾을 수 없습니다: {input_file}")
        sys.exit(1)

    with open(input_path, 'r', encoding='utf-8') as f:
        clusters = json.load(f)

    # 필터링
    print(f"\n{'='*60}")
    print(f"🔍 CV 결과 필터링")
    print(f"{'='*60}")
    print(f"입력: {len(clusters)}개 클러스터")
    print(f"최소 크기 임계값: {min_size} 픽셀\n")

    filtered, removed_count, removed_details = filter_cv_clusters(clusters, min_size)

    print(f"결과:")
    print(f"✅ 유지: {len(filtered)}개 클러스터")
    print(f"❌ 제거: {removed_count}개 클러스터")

    if show_removed and removed_details:
        print(f"\n제거된 항목:")
        for item in removed_details:
            print(f"  Cluster {item['cluster_id']}: '{item['text'][:30]}' ({item['size']}px)")
            print(f"    사유: {item['reason']}")

    # 결과 저장
    output_path = input_path.parent / f"{input_path.stem}_filtered.json"
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(filtered, f, ensure_ascii=False, indent=2)

    print(f"\n💾 저장: {output_path}")
    print(f"\n{'='*60}\n")


if __name__ == "__main__":
    main()
