#!/usr/bin/env python3
"""
Gemini Batch API 구현
- Inline requests 방식 (Cloud Storage 불필요)
- 50% 비용 절감
- 최대 20MB까지 지원
"""
import os
import json
import base64
import time
from pathlib import Path
from typing import List, Dict, Optional
from google import genai
from google.genai import types


class GeminiBatchProcessor:
    """Gemini Batch API를 사용한 이미지 처리"""
    
    def __init__(self, api_key: str = None):
        """
        Args:
            api_key: Gemini API 키 (None이면 환경변수에서 읽음)
        """
        if api_key is None:
            api_key = os.getenv('GEMINI_API_KEY')
        
        self.client = genai.Client(api_key=api_key)
        self.model = "gemini-3-pro-image-preview"  # Gemini 3 Pro (나노바나나)
    
    def create_batch_job(
        self,
        image_paths: List[str],
        prompt: str,
        job_name: str = "comic_translation_batch"
    ) -> str:
        """
        배치 작업 생성
        
        Args:
            image_paths: 이미지 파일 경로 리스트
            prompt: 공통 프롬프트
            job_name: 작업 이름
        
        Returns:
            batch_job_name: 배치 작업 ID
        """
        print(f"\n{'='*60}")
        print(f"📦 Gemini Batch Job 생성")
        print(f"{'='*60}")
        print(f"이미지 개수: {len(image_paths)}")
        print(f"모델: {self.model}")
        
        # Inline requests 생성
        requests = []
        total_size = 0
        
        for i, img_path in enumerate(image_paths):
            # 이미지를 base64로 인코딩
            with open(img_path, 'rb') as f:
                image_data = base64.b64encode(f.read()).decode('utf-8')
            
            # 파일 크기 체크
            file_size = len(image_data)
            total_size += file_size
            
            if total_size > 20 * 1024 * 1024:  # 20MB 제한
                print(f"⚠️  20MB 제한 초과: {i}개 이미지까지만 처리")
                break
            
            # Request 생성
            request = {
                "contents": [{
                    "parts": [
                        {"text": prompt},
                        {
                            "inline_data": {
                                "mime_type": "image/jpeg",
                                "data": image_data
                            }
                        }
                    ]
                }]
            }
            
            requests.append(request)
            
            if (i + 1) % 10 == 0:
                print(f"  진행: {i+1}/{len(image_paths)} ({total_size/1024/1024:.1f}MB)")
        
        print(f"\n총 {len(requests)}개 요청 생성 ({total_size/1024/1024:.1f}MB)")
        
        # Batch Job 생성
        print(f"\n배치 작업 제출 중...")
        
        try:
            batch_job = self.client.batches.create(
                model=self.model,
                requests=requests
            )
            
            print(f"✅ 배치 작업 생성 완료!")
            print(f"   Job Name: {batch_job.name}")
            print(f"   상태: {batch_job.state}")
            print(f"   생성 시간: {batch_job.create_time}")
            
            return batch_job.name
            
        except Exception as e:
            print(f"❌ 배치 작업 생성 실패: {e}")
            raise
    
    def check_batch_status(self, batch_job_name: str) -> Dict:
        """
        배치 작업 상태 확인
        
        Args:
            batch_job_name: 배치 작업 ID
        
        Returns:
            상태 정보 딕셔너리
        """
        try:
            batch_job = self.client.batches.get(name=batch_job_name)
            
            status = {
                'name': batch_job.name,
                'state': batch_job.state,
                'create_time': batch_job.create_time,
                'update_time': batch_job.update_time,
                'request_count': batch_job.request_count,
            }
            
            # 진행 상황이 있으면 추가
            if hasattr(batch_job, 'processed_count'):
                status['processed_count'] = batch_job.processed_count
                status['progress'] = f"{batch_job.processed_count}/{batch_job.request_count}"
            
            return status
            
        except Exception as e:
            print(f"❌ 상태 확인 실패: {e}")
            raise
    
    def wait_for_completion(
        self,
        batch_job_name: str,
        check_interval: int = 60,
        max_wait: int = 86400  # 24시간
    ) -> bool:
        """
        배치 작업 완료 대기
        
        Args:
            batch_job_name: 배치 작업 ID
            check_interval: 확인 간격 (초)
            max_wait: 최대 대기 시간 (초)
        
        Returns:
            성공 여부
        """
        print(f"\n{'='*60}")
        print(f"⏳ 배치 작업 완료 대기 중...")
        print(f"{'='*60}")
        
        start_time = time.time()
        
        while True:
            elapsed = time.time() - start_time
            
            if elapsed > max_wait:
                print(f"❌ 최대 대기 시간 초과 ({max_wait}초)")
                return False
            
            status = self.check_batch_status(batch_job_name)
            
            print(f"\n[{int(elapsed/60)}분 경과]")
            print(f"  상태: {status['state']}")
            if 'progress' in status:
                print(f"  진행: {status['progress']}")
            
            # 완료 상태 확인
            if status['state'] == 'STATE_SUCCEEDED':
                print(f"\n✅ 배치 작업 완료!")
                return True
            elif status['state'] == 'STATE_FAILED':
                print(f"\n❌ 배치 작업 실패")
                return False
            elif status['state'] == 'STATE_CANCELLED':
                print(f"\n⚠️  배치 작업 취소됨")
                return False
            
            # 대기
            print(f"  다음 확인: {check_interval}초 후...")
            time.sleep(check_interval)
    
    def get_batch_results(self, batch_job_name: str) -> List[Dict]:
        """
        배치 작업 결과 가져오기
        
        Args:
            batch_job_name: 배치 작업 ID
        
        Returns:
            결과 리스트
        """
        print(f"\n{'='*60}")
        print(f"📥 배치 결과 가져오기")
        print(f"{'='*60}")
        
        try:
            batch_job = self.client.batches.get(name=batch_job_name)
            
            # 결과 파일 다운로드
            results = []
            
            # Batch API는 결과를 순회 가능한 형태로 제공
            for i, response in enumerate(batch_job.responses):
                result = {
                    'index': i,
                    'success': response.status.code == 0,
                    'text': response.candidates[0].content.parts[0].text if response.candidates else None,
                    'error': response.status.message if response.status.code != 0 else None
                }
                results.append(result)
                
                if (i + 1) % 10 == 0:
                    print(f"  진행: {i+1} 결과 처리됨")
            
            print(f"\n✅ 총 {len(results)}개 결과 수신")
            
            # 성공/실패 통계
            success_count = sum(1 for r in results if r['success'])
            fail_count = len(results) - success_count
            
            print(f"   성공: {success_count}")
            print(f"   실패: {fail_count}")
            
            return results
            
        except Exception as e:
            print(f"❌ 결과 가져오기 실패: {e}")
            raise
    
    def list_batch_jobs(self, limit: int = 10) -> List[Dict]:
        """
        배치 작업 목록 조회
        
        Args:
            limit: 최대 개수
        
        Returns:
            작업 목록
        """
        try:
            jobs = self.client.batches.list(page_size=limit)
            
            job_list = []
            for job in jobs:
                job_info = {
                    'name': job.name,
                    'state': job.state,
                    'create_time': job.create_time,
                    'request_count': job.request_count
                }
                job_list.append(job_info)
            
            return job_list
            
        except Exception as e:
            print(f"❌ 목록 조회 실패: {e}")
            raise


# 사용 예시
if __name__ == "__main__":
    # 배치 프로세서 생성
    processor = GeminiBatchProcessor()
    
    # 테스트 이미지 경로
    image_paths = [
        "temp_outputs/archive/00029.jpg",
        # 더 많은 이미지 추가...
    ]
    
    # 프롬프트
    prompt = """이 만화 이미지에서 텍스트를 추출하고 한국어로 번역해주세요.
    JSON 형식으로 반환:
    {
        "bubbles": [
            {"text": "원문", "translation": "번역"}
        ]
    }"""
    
    # 1. 배치 작업 생성
    job_name = processor.create_batch_job(image_paths, prompt)
    
    # 2. 완료 대기 (옵션)
    # success = processor.wait_for_completion(job_name, check_interval=60)
    
    # 3. 상태 확인
    status = processor.check_batch_status(job_name)
    print(f"\n현재 상태: {status}")
    
    # 4. 결과 가져오기 (완료 후)
    # results = processor.get_batch_results(job_name)
    # for i, result in enumerate(results):
    #     print(f"Image {i}: {result['text'][:100]}...")
