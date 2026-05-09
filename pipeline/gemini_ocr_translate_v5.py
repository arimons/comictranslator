#!/usr/bin/env python3
"""
Gemini OCR + Translation + Integrated JSON 생성

Cloud Vision/cluster 결과를 위치 힌트로 사용하고, Gemini 한 번의 호출로
OCR, 번역, 문장 단위 cluster 매칭을 수행한다.

사용법:
  python3 pipeline/gemini_ocr_translate_v5.py <image_path> <cv_clusters.json> <output_integrated.json> [extra_prompt]

결과:
  - output_integrated.json: masking/overlay가 바로 사용할 integrated JSON
"""

import json
import sys
from pathlib import Path
from typing import List, Dict, Any
import google.generativeai as genai
from dotenv import load_dotenv
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# 환경 변수 로드
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    print("❌ GEMINI_API_KEY 환경 변수가 설정되어 있지 않습니다")
    sys.exit(1)

genai.configure(api_key=GEMINI_API_KEY)


def create_unified_prompt(cv_clusters: List[Dict], extra_prompt: str = "") -> str:
    """
    OCR + 번역 + cluster 매칭 통합 프롬프트 생성

    CV 클러스터는 위치 힌트로만 제공하고, Gemini가 이미지를 직접 보고 문장을 재구성한다.
    """

    # 클러스터 정보 추출 (bbox 포함)
    cluster_info = []
    for cluster in cv_clusters:
        cluster_id = cluster['cluster_id']
        lines = cluster.get('lines', [])
        text = ' '.join([line['text'] for line in lines])
        bbox = cluster.get('bounding_rect', [])
        cluster_info.append(f"Cluster {cluster_id} (bbox: {bbox}): {text}")

    clusters_text = "\n".join(cluster_info)

    prompt = f"""당신은 일본어 만화/웹툰 전문 번역가입니다.

📌 Cloud Vision API 감지 텍스트 (힌트):
{clusters_text}

⚠️ 아주 중요한 BBOX 병합 지침 (반드시 숙지하세요):
1. **CV 파편화 복원**: 시각적으로 한 개의 말풍선에 들어있는 1개의 완결된 문장이, Cloud Vision의 인식 오류(글자 누락 등)로 인해 여러 개의 CV 클러스터(ID)로 쪼개져 있을 수 있습니다.
2. 당신은 단순히 텍스트를 나열하는 것이 아니라 **이미지를 직접 보고 논리적으로 하나의 문장이 되는 조각들을 모두 찾아내어 완벽한 1개의 "text"와 "text_translated"로 병합**해야 합니다.
3. 병합된 문장의 출처가 된 모든 CV 클러스터 ID들을 `matched_cluster` 배열에 반드시 전부 담으세요. (예: CV 클러스터 1, 2, 3이 하나의 말풍선을 구성한다면 `[1, 2, 3]`으로 반환)

📝 번역 지침:
- 원문이 세로쓰기라도, 한국어 번역문은 항상 가로쓰기로 자연스럽게 작성하세요.
- 각 문장의 종류를 판별하세요 (dialogue: 일반 대사, sfx: 효과음, background: 배경글자)
- {extra_prompt}

반드시 다음 JSON Schema를 엄격히 준수하여 순수 JSON 포맷으로 응답하세요. Markdown 백틱(```json)을 사용하지 마세요.
{{
  "clusters": [
    {{
      "sentence_id": 1,
      "text": "일본어 완벽한 원문 (조각들 병합됨)",
      "text_translated": "한국어 번역문 (가로 읽기 구조)",
      "matched_cluster": [1, 2, 3],  // 이 문장을 구성하는데 사용된 CV 클러스터 ID 리스트 (1개 이상 필수)
      "orientation": "vertical",     // "vertical" 또는 "horizontal"
      "text_type": "dialogue"
    }}
  ]
}}
"""

    return prompt


def _process_single_ocr(image_path: str, cv_clusters_path: str,
                        output_integrated_path: str, extra_prompt: str = "") -> Dict[str, Any]:
    """
    단일 이미지 OCR + 번역 처리 (내부 함수)

    Returns:
        처리 결과 딕셔너리
    """
    img_name = Path(image_path).stem

    try:
        # CV 클러스터 로드
        with open(cv_clusters_path, 'r', encoding='utf-8') as f:
            cv_clusters = json.load(f)

        # 프롬프트 생성
        prompt = create_unified_prompt(cv_clusters, extra_prompt)

        # 이미지 로드
        img = Path(image_path)
        if not img.exists():
            return {
                "img_name": img_name,
                "success": False,
                "error": "이미지를 찾을 수 없음"
            }

        # Gemini API 호출
        api_start = time.time()
        model = genai.GenerativeModel('gemini-3-flash-preview')
        generation_config = genai.types.GenerationConfig(
            response_mime_type="application/json",
            temperature=0.2
        )

        with open(image_path, 'rb') as image_file:
            image_data = image_file.read()

        response = model.generate_content(
            [prompt, {'mime_type': 'image/jpeg', 'data': image_data}],
            generation_config=generation_config
        )

        api_elapsed = time.time() - api_start

        # 응답 파싱
        response_text = response.text.strip()
        
        try:
            result = json.loads(response_text)
        except json.JSONDecodeError:
            if "```" in response_text:
                clean_txt = response_text.split("```json")[-1].split("```")[0].strip()
                result = json.loads(clean_txt)
            else:
                raise

        # Integrated 포맷 변환 및 고립 클러스터 처리
        gemini_clusters = result.get('clusters', [])
        cv_cluster_map = {cv_c.get('cluster_id'): cv_c for cv_c in cv_clusters}
        
        # 사용된 모든 CV 클러스터 ID 추적
        used_cv_ids = set()
        final_clusters = []
        
        for idx, sentence in enumerate(gemini_clusters):
            matched_ids = sentence.get('matched_cluster', sentence.get('source_clusters', []))
            if not isinstance(matched_ids, list):
                matched_ids = [matched_ids] if matched_ids else []
            normalized_matched_ids = []
            for matched_id in matched_ids:
                try:
                    normalized_matched_ids.append(int(matched_id))
                except (TypeError, ValueError):
                    normalized_matched_ids.append(matched_id)
            matched_ids = normalized_matched_ids
                
            used_cv_ids.update(matched_ids)
            
            # Union Box 계산
            union_rects = []
            matched_cluster_details = []
            for m_id in matched_ids:
                cv_c = cv_cluster_map.get(m_id)
                if not cv_c:
                    continue
                if 'bounding_rect' in cv_c:
                    union_rects.append(cv_c['bounding_rect'])
                matched_cluster_details.append({
                    "cluster_id": cv_c.get("cluster_id"),
                    "text": cv_c.get("text", ""),
                    "bounding_rect": cv_c.get("bounding_rect", [0, 0, 0, 0]),
                    "lines": cv_c.get("lines", [])
                })
                        
            union_bbox = [0,0,0,0]
            if union_rects:
                xs = [r[0] for r in union_rects]
                ys = [r[1] for r in union_rects]
                max_xs = [r[0] + r[2] for r in union_rects]
                max_ys = [r[1] + r[3] for r in union_rects]
                min_x, min_y = min(xs), min(ys)
                max_x, max_y = max(max_xs), max(max_ys)
                union_bbox = [min_x, min_y, max_x - min_x, max_y - min_y]
                
            final_clusters.append({
                "sentence_id": sentence.get('sentence_id', idx + 1),
                "text": sentence.get('text', ''),
                "text_translated": sentence.get('text_translated', ''),
                "text_type": sentence.get('text_type', 'dialogue'),
                "orientation": sentence.get('orientation', 'vertical'),
                "matched_cluster": matched_ids,
                "matched_cluster_details": matched_cluster_details,
                "union_bbox": union_bbox,  # 병합된 전체 픽셀 BBox
                "bounding_rect": union_bbox,
                "location": "in_bubble",
                "match_score": 1.0
            })
            
        # 선택받지 못한 고아 CV 클러스터 수집 (Unmatched / Orphan Rescue 후보)
        unmatched_clusters = []
        for cv_c in cv_clusters:
            if cv_c.get('cluster_id') not in used_cv_ids:
                un_bbox = cv_c.get('bounding_rect')
                if not un_bbox:
                    pts = cv_c.get('bounding_poly', [{'x': 0, 'y': 0}])
                    xs = [p.get('x', 0) for p in pts]
                    ys = [p.get('y', 0) for p in pts]
                    un_bbox = [min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)]
                
                unmatched_clusters.append({
                    "cluster_id": cv_c.get('cluster_id'),
                    "text": cv_c.get('text', ''),
                    "bounding_rect": un_bbox
                })

        # 최종 _integrated.json 포맷 저장
        integrated_data = {
            "clusters": final_clusters,
            "unmatched_clusters": unmatched_clusters,
            "model": "gemini-3.0-flash (Hybrid V6 Spatial Union BBox)"
        }

        with open(output_integrated_path, 'w', encoding='utf-8') as f:
            json.dump(integrated_data, f, ensure_ascii=False, indent=2)

        return {
            "img_name": img_name,
            "success": True,
            "output_path": output_integrated_path,
            "sentences": len(final_clusters),
            "elapsed": api_elapsed
        }

    except json.JSONDecodeError as e:
        return {
            "img_name": img_name,
            "success": False,
            "error": f"JSON 파싱 실패: {str(e)[:50]}"
        }
    except Exception as e:
        return {
            "img_name": img_name,
            "success": False,
            "error": str(e)[:100]
        }


def process_ocr_and_translation_batch(image_paths: List[str], cv_clusters_paths: List[str],
                                     output_dir: str = "output",
                                     extra_prompt: str = "",
                                     max_workers: int = 8) -> Dict[str, Any]:
    """
    여러 이미지를 병렬로 OCR + 번역 처리

    Args:
        image_paths: 이미지 파일 경로 리스트
        cv_clusters_paths: CV 클러스터 JSON 경로 리스트 (image_paths와 같은 순서)
        output_dir: 출력 디렉토리
        extra_prompt: 추가 프롬프트
        max_workers: 동시 처리 워커 수 (기본값: 8)

    Returns:
        처리 결과 딕셔너리
    """
    print(f"\n{'='*60}")
    print(f"🚀 Gemini OCR + Translation 병렬 처리 시작")
    print(f"{'='*60}")
    print(f"📊 이미지 개수: {len(image_paths)}개")
    print(f"⚙️ 워커: {max_workers}개 병렬 처리")
    print(f"📁 출력 폴더: {output_dir}\n")

    output_dir_path = Path(output_dir)
    output_dir_path.mkdir(parents=True, exist_ok=True)

    results = []
    completed = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}

        for idx, (img_path, cv_path) in enumerate(zip(image_paths, cv_clusters_paths)):
            img_name = Path(img_path).stem
            output_integrated_path = output_dir_path / f"{img_name}_integrated.json"

            future = executor.submit(
                _process_single_ocr,
                img_path, cv_path, str(output_integrated_path), extra_prompt
            )
            futures[future] = img_name

        # 완료된 것부터 수집
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            completed += 1

            img_name = futures[future]
            if result.get('success'):
                print(f"  ✅ {completed}/{len(image_paths)} ({img_name}) - {result.get('sentences', 0)}개 문장")
            else:
                print(f"  ❌ {completed}/{len(image_paths)} ({img_name}) - {result.get('error', 'Unknown error')[:40]}")

    # 통계
    success_count = sum(1 for r in results if r.get('success'))
    failed_count = sum(1 for r in results if not r.get('success'))
    total_sentences = sum(r.get('sentences', 0) for r in results if r.get('success'))

    print(f"\n{'='*60}")
    print(f"✅ OCR + 번역 병렬 처리 완료!")
    print(f"   📊 성공: {success_count}/{len(results)}")
    print(f"   📝 총 문장: {total_sentences}개")
    if failed_count > 0:
        print(f"   ❌ 실패: {failed_count}개")
    print(f"{'='*60}\n")

    return {
        "total": len(results),
        "success": success_count,
        "failed": failed_count,
        "total_sentences": total_sentences,
        "results": results
    }


def process_ocr_and_translation(image_path: str, cv_clusters_path: str,
                               output_integrated_path: str,
                               extra_prompt: str = "") -> bool:
    """
    OCR + Translation 통합 처리

    한 번의 API 호출로 OCR과 번역을 처리하고 하나의 JSON 파일 생성
    """
    
    print(f"\n{'='*60}")
    print(f"🚀 Gemini OCR + Translation + Integrated JSON 생성 시작")
    print(f"{'='*60}")
    
    result = _process_single_ocr(image_path, cv_clusters_path, output_integrated_path, extra_prompt)
    
    if result.get("success"):
        print(f"✅ OCR + 번역 성공! (소요시간: {result.get('elapsed', 0):.1f}초)")
        return True
    else:
        print(f"❌ OCR + 번역 실패: {result.get('error', 'Unknown Error')}")
        return False


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: python3 gemini_ocr_translate_v5.py <image_path> <cv_clusters.json> <output_integrated.json> [extra_prompt]")
        sys.exit(1)

    image_path = sys.argv[1]
    cv_clusters_path = sys.argv[2]
    output_integrated_path = sys.argv[3]
    extra_prompt = sys.argv[4] if len(sys.argv) > 4 else ""

    success = process_ocr_and_translation(
        image_path, cv_clusters_path,
        output_integrated_path,
        extra_prompt
    )

    sys.exit(0 if success else 1)
