#!/usr/bin/env python3
"""
Gemini AI로 만화책 표지 번역
복잡한 레이아웃의 표지 이미지를 Gemini 3 Pro Image Preview로 자연스럽게 번역
"""

import sys
import os
import time
from pathlib import Path
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

def translate_cover(image_path: str, output_path: str, model_name: str = 'gemini-3-pro-image-preview'):
    """
    표지 이미지를 Gemini AI로 번역
    
    Args:
        image_path: 원본 이미지 경로
        output_path: 결과 이미지 저장 경로
        model_name: 사용할 Gemini 모델명
    """
    # API 키 설정
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("❌ GEMINI_API_KEY 환경변수가 필요합니다")
        sys.exit(1)
    
    genai.configure(api_key=api_key)
    
    # 선택된 모델 사용
    model = genai.GenerativeModel(model_name)
    
    print(f"📖 표지 번역 시작: {Path(image_path).name}")
    print(f"🤖 모델: {model_name}")
    
    # 이미지 업로드
    print(f"  📤 이미지 업로드 중...")
    image_file = genai.upload_file(image_path)
    
    # 프롬프트
    prompt = """이 만화책 표지 이미지를 한국어로 번역해주세요.

**요구사항:**
1. **모든 일본어 텍스트를 한국어로 번역**
   - 제목, 작가명, 출판사, 캐치프레이즈 등 모든 텍스트
   
2. **원본 디자인 완벽 유지**
   - 폰트 스타일 (굵기, 기울임, 장식 등)
   - 텍스트 색상 및 효과 (그림자, 외곽선 등)
   - 레이아웃 및 배치
   - 배경 디자인 및 일러스트
   
3. **자연스러운 한국어 번역**
   - 만화 제목은 자연스럽게 의역
   - 캐치프레이즈는 한국 독자에게 어필할 수 있도록
   - 존댓말/반말 적절히 선택
   
4. **고품질 출력**
   - 원본과 동일한 해상도
   - 가능한 한 높은 화질

**중요:**
- 텍스트만 번역하고 이미지 자체는 변경하지 마세요
- 원본의 예술적 느낌을 그대로 유지하세요
- 번역 결과 이미지를 직접 생성해서 반환해주세요

번역된 표지 이미지를 생성해주세요."""

    # API 호출 (타임아웃 및 재시도)
    print(f"  🔄 Gemini AI 처리 중...")
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            if attempt > 0:
                print(f"     재시도 {attempt}/{max_retries-1}...")
                time.sleep(5)
            
            response = model.generate_content(
                [prompt, image_file],
                generation_config=genai.types.GenerationConfig(
                    temperature=0.4,
                    top_p=0.95,
                    top_k=40,
                    max_output_tokens=8192,
                ),
                request_options={"timeout": 300}
            )
            
            # 성공 - 응답 처리
            if not response:
                if attempt < max_retries - 1:
                    print(f"     ⚠️  빈 응답, 재시도 중...")
                    continue
                print("❌ Gemini 응답이 비어있습니다")
                return False

            # 응답에서 이미지 추출
            image_found = False
            if hasattr(response, 'parts') and response.parts:
                for part in response.parts:
                    try:
                        # inline_data 확인 (이미지 데이터)
                        if hasattr(part, 'inline_data'):
                            # MIME type 확인
                            mime_type = getattr(part.inline_data, 'mime_type', 'unknown')
                            print(f"  ℹ️ 파트 타입: {mime_type}")

                            # 이미지 데이터 추출
                            if mime_type and 'image' in mime_type:
                                image_data = part.inline_data.data
                                if isinstance(image_data, bytes) and len(image_data) > 100:
                                    with open(output_path, 'wb') as f:
                                        f.write(image_data)
                                    print(f"\n✅ 번역 완료: {output_path}")
                                    return True
                    except Exception as part_error:
                        print(f"  ℹ️ 파트 처리 오류: {str(part_error)[:100]}")
                        continue

            # 텍스트 응답 확인
            if hasattr(response, 'text') and response.text:
                print(f"\n📝 Gemini 응답 (텍스트):")
                print(response.text[:500])
                # 텍스트만 반환된 경우
                guide_path = Path(output_path).with_suffix('.txt')
                with open(guide_path, 'w', encoding='utf-8') as f:
                    f.write("표지 번역 결과:\n\n")
                    f.write(response.text)
                print(f"  📄 번역 가이드 저장: {guide_path}")
                return False

            # 이미지도 텍스트도 없음
            print("\n⚠️  응답에 이미지나 텍스트가 없습니다.")
            return False
            
        except Exception as e:
            error_msg = str(e)
            if attempt < max_retries - 1:
                print(f"     ⚠️  오류: {error_msg[:100]}, 재시도 중...")
                continue
            print(f"❌ 오류 발생: {error_msg}")
            return False
    
    return False

def main():
    if len(sys.argv) < 2:
        print("사용법: python3 translate_cover.py <image_path> [output_path]")
        print("\n예시:")
        print("  python3 translate_cover.py cover.jpg")
        print("  python3 translate_cover.py cover.jpg output/cover_ko.jpg")
        sys.exit(1)
    
    image_path = sys.argv[1]
    
    if not Path(image_path).exists():
        print(f"❌ 파일을 찾을 수 없습니다: {image_path}")
        sys.exit(1)
    
    # 출력 경로 결정
    if len(sys.argv) >= 3:
        output_path = sys.argv[2]
    else:
        p = Path(image_path)
        output_path = str(p.parent / f"{p.stem}_ko{p.suffix}")
    
    # 출력 디렉토리 생성
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    
    # 번역 실행
    success = translate_cover(image_path, output_path)
    
    if success:
        print("\n🎉 표지 번역 완료!")
    else:
        print("\n⚠️  이미지 생성 실패 - 텍스트 가이드를 참고하세요")

if __name__ == "__main__":
    main()
