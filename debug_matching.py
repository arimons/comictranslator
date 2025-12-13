#!/usr/bin/env python3
"""
매칭 로직 디버그 스크립트
개별 파일로 빠르게 테스트
"""
import json
import sys

# 테스트 데이터
cv_clusters = [
    {
        "cluster_id": 1,
        "orientation": "vertical",
        "lines": [
            {"text": "囲まれて", "bbox": [978, 168, 35, 164]},
            {"text": "しまった様だな・・・", "bbox": [913, 168, 39, 332]}
        ],
        "bounding_rect": [913, 168, 121, 332]
    },
    {
        "cluster_id": 2,
        "orientation": "vertical",
        "lines": [
            {"text": "濃", "bbox": [653, 822, 45, 31]}
        ],
        "bounding_rect": [653, 822, 72, 31]
    },
    {
        "cluster_id": 3,
        "orientation": "vertical",
        "lines": [
            {"text": "なっなんなの", "bbox": [331, 979, 26, 191]},
            {"text": "これ…っ", "bbox": [293, 981, 26, 109]}
        ],
        "bounding_rect": [293, 979, 79, 191]
    }
]

sentences = [
    {"sentence_id": 1, "text": "囲まれて しまった様だな・・・"},
    {"sentence_id": 2, "text": "濃"},
    {"sentence_id": 3, "text": "なっなんなの これ…っ"}
]

def split_by_newline(text: str):
    """기존 로직: 개행으로만 분리"""
    chunks = [chunk.strip() for chunk in text.split('\n') if chunk.strip()]
    return chunks

def split_by_space(text: str):
    """개선된 로직: 개행 AND 공백으로 분리"""
    # 먼저 개행으로 분리
    by_newline = text.split('\n')
    chunks = []
    for part in by_newline:
        # 각 부분을 공백으로 분리
        sub_chunks = part.split(' ')
        for chunk in sub_chunks:
            chunk = chunk.strip()
            if chunk:
                chunks.append(chunk)
    return chunks

print("=" * 60)
print("청크 분리 로직 비교")
print("=" * 60)

for sentence in sentences:
    sent_id = sentence['sentence_id']
    text = sentence['text']

    chunks_newline = split_by_newline(text)
    chunks_space = split_by_space(text)

    print(f"\n📝 Sentence {sent_id}: '{text}'")
    print(f"  기존 (개행만): {chunks_newline}")
    print(f"  개선 (개행+공백): {chunks_space}")

    # 해당 cluster 찾기
    for cluster in cv_clusters:
        if cluster['cluster_id'] == sent_id:
            cv_lines = [line['text'] for line in cluster['lines']]
            print(f"  CV Lines: {cv_lines}")

            # 비교
            print(f"  → 기존 방식: {len(chunks_newline)} chunks vs {len(cv_lines)} CV lines")
            print(f"  → 개선 방식: {len(chunks_space)} chunks vs {len(cv_lines)} CV lines")

print("\n" + "=" * 60)
print("결론:")
print("공백 기준 분리가 필요!")
print("=" * 60)
