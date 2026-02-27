#!/usr/bin/env python3
"""
Gemini Batch API - Inline Requests 방식
- 여러 이미지를 한 번의 API 호출로 처리
- 50% 비용 절감
- 즉시 응답
"""
import os
import json
import time
import traceback
import base64
import threading
from pathlib import Path
from typing import List, Dict, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import google.generativeai as genai


class GeminiBatchProcessor:
    """Gemini Batch API - Rate Limit (RPM/TPM) 관리형 병렬 처리"""

    def __init__(self, api_key: str = None, max_workers: int = 3, rpm_limit: int = 10, model_name: str = "gemini-3-pro-image-preview"):
        """
        Args:
            api_key: Gemini API 키 (None이면 환경변수에서 읽음)
            max_workers: 병렬 처리 워커 수
            rpm_limit: 분당 최대 요청 수 (Preview 모델을 위해 보수적으로 10~15 추천)
            model_name: 실행할 AI 모델명
        """
        if api_key is None:
            api_key = os.getenv('GEMINI_API_KEY')

        if not api_key:
            raise ValueError("GEMINI_API_KEY 환경변수가 필요합니다")

        genai.configure(api_key=api_key)
        self.model = model_name
        self.max_workers = max_workers
        self.rpm_limit = rpm_limit
        
        # Rate Limiting 관리용 변수
        self.request_timestamps = []
        self.rate_limit_lock = threading.Lock()

    def _enforce_rate_limit(self):
        """요청 전 Rate Limit을 확인하고 필요 시 대기합니다."""
        with self.rate_limit_lock:
            now = time.time()
            # 60초가 지난 과거 기록은 제거
            self.request_timestamps = [t for t in self.request_timestamps if now - t < 60]
            
            if len(self.request_timestamps) >= self.rpm_limit:
                # RPM 제한에 도달한 경우, 가장 오래된 요청이 60초가 지날 때까지 대기
                oldest_request = self.request_timestamps[0]
                sleep_time = 60 - (now - oldest_request)
                if sleep_time > 0:
                    print(f"   ⏳ [Rate Limit 보호] RPM({self.rpm_limit}) 도달. 구글 서버 안정화를 위해 {sleep_time:.1f}초 대기...")
                    time.sleep(sleep_time)
            
            # 새로운 요청 타임스탬프 기록
            self.request_timestamps.append(time.time())

    def _upload_single_file(self, img_path: str) -> Dict:
        """단일 파일 업로드 (병렬 처리용)"""
        img_name = Path(img_path).stem
        try:
            # 업로드하는 과정도 Google API 를 호출하므로 Rate Limit 에 적용될 수 있음
            self._enforce_rate_limit()
            image_file = genai.upload_file(img_path)
            return {
                "success": True,
                "img_name": img_name,
                "img_path": img_path,
                "image_file": image_file
            }
        except Exception as e:
            return {
                "success": False,
                "img_name": img_name,
                "error": str(e)
            }

    def _generate_single_image(self, req_data: Dict) -> Dict:
        """
        단일 이미지 생성 (병렬 처리용, 재시도 없음)

        Args:
            req_data: 요청 데이터

        Returns:
            결과 딕셔너리 (실패 시 retryable 필드 포함)
        """
        try:
            # 🚨 구글 API 요청 직전에 RPM 제한을 철저히 확인하여 슬립 수행
            self._enforce_rate_limit()
            
            model = genai.GenerativeModel(self.model)
            response = model.generate_content(
                req_data["request"]["contents"][0]["parts"],
                generation_config=genai.types.GenerationConfig(
                    temperature=0.4,
                    top_p=0.95,
                    top_k=40,
                    max_output_tokens=2048,
                ),
                request_options={"timeout": 240}
            )

            # 이미지 응답 확인
            response_text = ""

            if hasattr(response, 'parts') and response.parts:
                for part in response.parts:
                    if hasattr(part, 'inline_data'):
                        mime_type = getattr(part.inline_data, 'mime_type', 'unknown')
                        if 'image' in mime_type:
                            image_data = part.inline_data.data
                            return {
                                "index": req_data["index"],
                                "img_name": req_data['img_name'],
                                "success": True,
                                "has_image": True,
                                "mime_type": mime_type,
                                "image_data": image_data
                            }
                    elif hasattr(part, 'text'):
                        response_text = part.text

            # 텍스트만 반환된 경우
            return {
                "index": req_data["index"],
                "img_name": req_data['img_name'],
                "success": bool(response_text),
                "has_image": False,
                "text": response_text[:200]
            }

        except Exception as e:
            error_str = str(e)
            error_code = None

            # 500 에러 감지
            if "500" in error_str or "InternalServerError" in error_str:
                error_code = "500_INTERNAL_SERVER_ERROR"
            elif "429" in error_str or "ResourceExhausted" in error_str:
                error_code = "429_RATE_LIMIT"
            elif "503" in error_str or "Unavailable" in error_str:
                error_code = "503_SERVICE_UNAVAILABLE"
            elif "deadline" in error_str.lower() or "timeout" in error_str.lower():
                error_code = "TIMEOUT"

            # 재시도 가능 에러 여부만 표시
            return {
                "index": req_data["index"],
                "img_name": req_data['img_name'],
                "success": False,
                "error": error_str[:150],
                "error_code": error_code,
                "retryable": error_code is not None
            }

    def _process_end_to_end(self, img_path: str, prompt: str, index: int, output_dir_str: str) -> Dict:
        """단일 이미지에 대해 업로드부터 생성까지 한 번에 엔드투엔드로 처리하고, 즉시 저장합니다."""
        img_name = Path(img_path).stem
        output_dir = Path(output_dir_str)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{img_name}_ai.jpg"
        
        # 1. 파일 업로드 단계
        try:
            self._enforce_rate_limit()
            image_file = genai.upload_file(img_path)
        except Exception as e:
            error_str = str(e)
            retryable = "429" in error_str or "500" in error_str or "503" in error_str or "timeout" in error_str.lower()
            return {
                "index": index,
                "img_name": img_name,
                "success": False,
                "error": f"Upload failed: {error_str}"[:150],
                "error_code": "UPLOAD_FAIL",
                "retryable": retryable
            }

        # 2. 이미지 생성 단계
        req_data = {
            "index": index,
            "img_name": img_name,
            "request": {
                "contents": [{
                    "parts": [
                        {"text": prompt},
                        {
                            "file_data": {
                                "mime_type": image_file.mime_type,
                                "file_uri": image_file.uri
                            }
                        }
                    ],
                    "role": "user"
                }]
            }
        }
        
        # 3. 단일 이미지 생성 및 즉시 디스크 저장
        result = self._generate_single_image(req_data)
        
        if result.get('has_image') and result.get('image_data'):
            try:
                image_data = result['image_data']
                if isinstance(image_data, str):
                    image_bytes = base64.b64decode(image_data)
                elif isinstance(image_data, bytes):
                    image_bytes = image_data
                else:
                    result['success'] = False
                    result['error'] = f"알 수 없는 이미지 데이터 형식: {type(image_data).__name__}"
                    return result
                
                with open(output_path, 'wb') as f:
                    f.write(image_bytes)
                result['saved_path'] = str(output_path)
            except Exception as e:
                result['success'] = False
                result['error'] = f"이미지 저장 실패: {str(e)}"
                
        elif result.get('text'):
            txt_path = output_dir / f"{img_name}_ai.txt"
            try:
                with open(txt_path, 'w', encoding='utf-8') as f:
                    f.write(result['text'])
                result['saved_path'] = str(txt_path)
            except Exception as e:
                result['success'] = False
                result['error'] = f"텍스트 저장 실패: {str(e)}"
                
        return result

    def create_batch_job(
        self,
        image_paths: List[str],
        prompt: str,
        output_dir: str,
        job_name: str = "ai_translation_batch"
    ) -> Dict:
        """
        병렬 처리 기반 배치 작업 생성

        Args:
            image_paths: 이미지 파일 경로 리스트
            prompt: 공통 프롬프트
            job_name: 작업 이름 (표시용)

        Returns:
            배치 결과 딕셔너리
        """
        print(f"\n{'='*60}")
        print(f"🚀 스마트 Batch 처리 (Rate Limit 보호 모드)")
        print(f"{'='*60}")
        print(f"📊 이미지 개수: {len(image_paths)}개")
        print(f"🤖 모델: {self.model}")
        print(f"⚙️ 병렬 워커: {self.max_workers}개")
        print(f"🛡️ 분당 제한: 최대 {self.rpm_limit} 요청 (RPM)")

        # Step 1: 병렬 엔드투엔드 처리 (업로드 + 생성)
        print(f"\n🔄 병렬 통합 처리 시작 ({self.max_workers}개 동시 처리)...")
        results = []

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(self._process_end_to_end, img_path, prompt, i, output_dir): i
                for i, img_path in enumerate(image_paths)
            }

            completed = 0
            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                completed += 1

                if result.get("has_image"):
                    print(f"  ✅ {completed}/{len(image_paths)} ({result['img_name']}) - 완료")
                elif result.get("success"):
                    print(f"  ⚠️ {completed}/{len(image_paths)} ({result['img_name']}) - 텍스트 응답")
                else:
                    print(f"  ❌ {completed}/{len(image_paths)} ({result['img_name']}) - {result.get('error', 'Unknown error')[:50]}")

        print(f"\n✅ 1차 병렬 처리 완료!")

        # Step 3: 재시도 가능한 항목 필터링 및 재처리
        retryable_results = [r for r in results if r.get('retryable')]

        if retryable_results:
            print(f"\n⏳ 재시도 가능한 항목: {len(retryable_results)}개")
            print(f"🔄 재시도 처리 중...\n")

            for retry_count in range(3):  # 최대 3회 재시도
                wait_time = (2 ** retry_count)  # Exponential backoff: 1s, 2s, 4s
                print(f"   ⏳ {wait_time}초 대기 후 재시도 ({retry_count + 1}/3)...")
                time.sleep(wait_time)

                new_retryable = []
                with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                    retry_futures = {}
                    for result in retryable_results:
                        img_path = next((p for p in image_paths if Path(p).stem == result['img_name']), None)
                        if img_path:
                            retry_futures[executor.submit(self._process_end_to_end, img_path, prompt, result['index'], output_dir)] = result['img_name']

                    for future in as_completed(retry_futures):
                        img_name = retry_futures[future]
                        retry_result = future.result()

                        # 성공한 경우 결과 업데이트
                        if retry_result.get("success"):
                            print(f"     ✅ {img_name} - 재시도 성공")
                            # 기존 결과 대체
                            results = [r for r in results if r['img_name'] != img_name]
                            results.append(retry_result)
                        else:
                            # 재시도 가능하면 다음 라운드에 포함
                            if retry_result.get('retryable'):
                                new_retryable.append(retry_result)
                                print(f"     ❌ {img_name} - 실패 (재시도 예정)")
                            else:
                                results = [r for r in results if r['img_name'] != img_name]
                                results.append(retry_result)
                                print(f"     ❌ {img_name} - 재시도 불가능")

                retryable_results = new_retryable
                
                # 추가로 재시도할 항목이 없으면 조기 종료
                if not retryable_results:
                    break

            print(f"\n✅ 재시도 완료!")

        # 통계
        success_count = sum(1 for r in results if r.get('success'))
        image_count = sum(1 for r in results if r.get('has_image'))
        failed_count = sum(1 for r in results if not r.get('success'))
        retryable_count = sum(1 for r in results if r.get('retryable'))

        print(f"   📊 성공: {success_count}/{len(results)}")
        print(f"   🖼️ 이미지: {image_count}/{success_count}")

        if failed_count > 0:
            print(f"   ❌ 실패: {failed_count}개")
            if retryable_count > 0:
                print(f"   ⏳ 재시도 가능: {retryable_count}개 (서버/네트워크 오류)")

        return {
            "job_name": job_name,
            "state": "COMPLETED",
            "total_requests": len(image_paths),
            "results": results,
            "success": True,
            "stats": {
                "success": success_count,
                "failed": failed_count,
                "retryable": retryable_count
            }
        }

    def save_results(
        self,
        batch_result: Dict,
        output_dir: str = "output"
    ) -> List[str]:
        """
        배치 결과를 파일로 저장

        Args:
            batch_result: create_batch_job 반환값
            output_dir: 출력 디렉토리

        Returns:
            저장된 파일 경로 리스트
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        saved_files = []

        if not batch_result.get('success'):
            print(f"❌ 배치 실패: {batch_result.get('error')}")
            return []

        print(f"\n💾 결과 저장 중...")

        for result in batch_result.get('results', []):
            if result.get('has_image') and result.get('image_data'):
                img_name = result['img_name']
                output_path = output_dir / f"{img_name}_ai.jpg"

                try:
                    image_data = result['image_data']

                    # image_data 형식 변환: str(base64) → bytes
                    if isinstance(image_data, str):
                        # Base64 디코딩
                        image_bytes = base64.b64decode(image_data)
                    elif isinstance(image_data, bytes):
                        image_bytes = image_data
                    else:
                        print(f"  ⚠️ {img_name}: 예상치 못한 데이터 형식 ({type(image_data).__name__})")
                        continue

                    # 이미지 바이너리 저장
                    with open(output_path, 'wb') as f:
                        f.write(image_bytes)

                    size_mb = len(image_bytes) / 1024 / 1024
                    saved_files.append(str(output_path))
                    print(f"  ✅ {output_path.name} ({size_mb:.2f}MB)")
                except Exception as e:
                    print(f"  ❌ {img_name}: {str(e)[:60]}")
            elif result.get('text'):
                # 텍스트 결과 저장
                img_name = result['img_name']
                output_path = output_dir / f"{img_name}_ai.txt"

                try:
                    with open(output_path, 'w', encoding='utf-8') as f:
                        f.write(result['text'])
                    saved_files.append(str(output_path))
                    print(f"  📄 {output_path.name}")
                except Exception as e:
                    print(f"  ❌ {img_name}: {str(e)[:60]}")

        print(f"✅ {len(saved_files)}개 파일 저장 완료")
        return saved_files


# 사용 예시
if __name__ == "__main__":
    # 배치 프로세서 생성
    processor = GeminiBatchProcessor()

    # 테스트 이미지 경로
    image_paths = [
        # "test_image/050.jpg",
        # "test_image/051.jpg",
        # 더 많은 이미지 추가...
    ]

    if image_paths:
        # 프롬프트
        prompt = """이 일본 만화책 이미지를 한국어 가로쓰기로 번역된 새로운 이미지로 생성해주세요.

요구사항:
1. 모든 일본어 텍스트를 자연스러운 한국어로 번역
   - 제목, 작가명, 출판사, 캐치프레이즈, 말풍선 등 모든 텍스트
2. 한글은 **가로쓰기**로만 배치 (세로쓰기 금지)
3. 원본의 모든 특징 유지
   - 디자인, 레이아웃, 구성
   - 폰트 스타일 (굵기, 기울임, 장식)
   - 텍스트 색상 및 효과 (그림자, 외곽선)
   - 배경 일러스트와 캐릭터
4. 자연스러운 번역
   - 만화 제목은 한국 독자를 위해 자연스럽게 의역
   - 캐치프레이즈는 한국 독자에게 어필할 수 있도록
5. 고품질 출력
   - 원본과 동일한 해상도
   - 가능한 한 높은 화질

번역된 이미지를 생성해주세요."""

        # 배치 작업 실행
        batch_result = processor.create_batch_job(image_paths, prompt)

        # 결과 저장
        if batch_result.get('success'):
            saved_files = processor.save_results(batch_result, "output")
            print(f"\n📁 저장된 파일: {len(saved_files)}개")
