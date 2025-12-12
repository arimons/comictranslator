#!/usr/bin/env python3
"""
main.py에 추가할 Batch API 함수들
"""

def batch_translate_images_async(
    image_files: List,
    extra_prompt: str = "",
    font_name: str = "기본 폰트",
    font_size: int = 33,
    output_subfolder: str = "",
    progress=gr.Progress()
) -> tuple:
    """
    Gemini Batch API를 사용한 비동기 번역
    - 50% 비용 절감
    - 24시간 내 처리
    - 최대 200,000개 요청
    
    Returns:
        (job_name, status_message)
    """
    from gemini_batch_api import GeminiBatchProcessor
    import os
    
    log = []
    
    try:
        # API 키 확인
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            return None, "❌ GEMINI_API_KEY 환경변수가 설정되지 않았습니다."
        
        # 이미지 경로 추출
        image_paths = [f.name for f in image_files]
        n_images = len(image_paths)
        
        log.append(f"📦 Batch API 번역 시작")
        log.append(f"   이미지: {n_images}개")
        log.append(f"   비용: 실시간 대비 50% 절감")
        log.append("")
        
        # 프롬프트 구성
        base_prompt = """이 만화 이미지를 분석하여 다음 작업을 수행해주세요:

1. 모든 텍스트 영역(말풍선, 나레이션 등)을 찾아주세요
2. 각 텍스트를 한국어로 번역해주세요
3. JSON 형식으로 반환해주세요

JSON 형식:
{
  "sentences": [
    {
      "sentence_id": 1,
      "text": "원문 텍스트",
      "translation": "한국어 번역",
      "text_type": "dialogue/narration/sound_effect",
      "location": "in_bubble/outside_bubble"
    }
  ]
}"""
        
        if extra_prompt:
            base_prompt += f"\n\n추가 지시사항:\n{extra_prompt}"
        
        # Batch Processor 생성
        processor = GeminiBatchProcessor(api_key=api_key)
        
        # Batch Job 생성
        progress(0.1, desc="배치 작업 생성 중...")
        job_name = processor.create_batch_job(
            image_paths=image_paths,
            prompt=base_prompt,
            job_name=f"comic_translation_{int(time.time())}"
        )
        
        log.append(f"✅ 배치 작업 생성 완료")
        log.append(f"   Job ID: {job_name}")
        log.append("")
        log.append(f"⏳ 처리 중... (24시간 내 완료)")
        log.append(f"   상태 확인: batch_check_status('{job_name}')")
        log.append("")
        log.append(f"💡 Tip:")
        log.append(f"   - 배치 작업은 백그라운드에서 처리됩니다")
        log.append(f"   - 완료 후 batch_get_results()로 결과를 가져오세요")
        log.append(f"   - 비용: 실시간 API 대비 50% 절감")
        
        return job_name, "\n".join(log)
        
    except Exception as e:
        log.append(f"❌ 오류 발생: {str(e)}")
        return None, "\n".join(log)


def batch_check_status(job_name: str) -> str:
    """
    Batch 작업 상태 확인
    
    Args:
        job_name: 배치 작업 ID
    
    Returns:
        상태 메시지
    """
    from gemini_batch_api import GeminiBatchProcessor
    import os
    
    try:
        api_key = os.getenv("GEMINI_API_KEY")
        processor = GeminiBatchProcessor(api_key=api_key)
        
        status = processor.check_batch_status(job_name)
        
        msg = f"""
📊 배치 작업 상태

Job ID: {status['name']}
상태: {status['state']}
생성 시간: {status['create_time']}
업데이트: {status['update_time']}
요청 수: {status['request_count']}
"""
        
        if 'progress' in status:
            msg += f"진행: {status['progress']}\n"
        
        if status['state'] == 'STATE_SUCCEEDED':
            msg += "\n✅ 완료! batch_get_results()로 결과를 가져오세요."
        elif status['state'] == 'STATE_FAILED':
            msg += "\n❌ 실패"
        elif status['state'] in ['STATE_PENDING', 'STATE_RUNNING']:
            msg += "\n⏳ 처리 중..."
        
        return msg
        
    except Exception as e:
        return f"❌ 오류: {str(e)}"


def batch_get_results(
    job_name: str,
    output_subfolder: str = "",
    font_name: str = "기본 폰트",
    font_size: int = 33
) -> tuple:
    """
    Batch 작업 결과 가져오기 및 이미지 생성
    
    Args:
        job_name: 배치 작업 ID
        output_subfolder: 출력 폴더
        font_name: 폰트 이름
        font_size: 폰트 크기
    
    Returns:
        (gallery_images, status_message)
    """
    from gemini_batch_api import GeminiBatchProcessor
    import os
    import json
    from pathlib import Path
    
    log = []
    
    try:
        api_key = os.getenv("GEMINI_API_KEY")
        processor = GeminiBatchProcessor(api_key=api_key)
        
        # 결과 가져오기
        log.append("📥 배치 결과 가져오는 중...")
        results = processor.get_batch_results(job_name)
        
        log.append(f"✅ {len(results)}개 결과 수신")
        
        # 결과 처리
        output_images = []
        success_count = 0
        fail_count = 0
        
        for i, result in enumerate(results):
            if result['success']:
                # JSON 파싱
                try:
                    data = json.loads(result['text'])
                    
                    # 결과 저장 (기존 파이프라인과 동일한 형식)
                    # TODO: 이미지 번호와 매칭하여 파일 저장
                    
                    success_count += 1
                    
                except json.JSONDecodeError:
                    log.append(f"  ⚠️  Image {i}: JSON 파싱 실패")
                    fail_count += 1
            else:
                log.append(f"  ❌ Image {i}: {result['error']}")
                fail_count += 1
        
        log.append("")
        log.append(f"📊 처리 완료")
        log.append(f"   성공: {success_count}")
        log.append(f"   실패: {fail_count}")
        
        return output_images, "\n".join(log)
        
    except Exception as e:
        log.append(f"❌ 오류: {str(e)}")
        return [], "\n".join(log)


def batch_list_jobs() -> str:
    """
    배치 작업 목록 조회
    
    Returns:
        작업 목록 메시지
    """
    from gemini_batch_api import GeminiBatchProcessor
    import os
    
    try:
        api_key = os.getenv("GEMINI_API_KEY")
        processor = GeminiBatchProcessor(api_key=api_key)
        
        jobs = processor.list_batch_jobs(limit=20)
        
        if not jobs:
            return "📭 배치 작업이 없습니다."
        
        msg = f"📋 배치 작업 목록 (최근 {len(jobs)}개)\n\n"
        
        for i, job in enumerate(jobs, 1):
            msg += f"{i}. {job['name']}\n"
            msg += f"   상태: {job['state']}\n"
            msg += f"   생성: {job['create_time']}\n"
            msg += f"   요청: {job['request_count']}개\n\n"
        
        return msg
        
    except Exception as e:
        return f"❌ 오류: {str(e)}"
