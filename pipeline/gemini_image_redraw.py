# 복잡한 레이아웃 이미지를 Gemini 생성형 AI로 직접 재생성하여 번역
#!/usr/bin/env python3

import sys
import os
import time
from collections.abc import Callable
from pathlib import Path
import google.genai as genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()

LogCallback = Callable[[str], None]


def _emit(message: str, log_callback: LogCallback | None = None) -> None:
    print(message, flush=True)
    if log_callback is not None:
        log_callback(message)


def redraw_image(
    image_path: str,
    output_path: str,
    model_name: str = 'gemini-3-pro-image-preview',
    log_callback: LogCallback | None = None,
):
    """
    표지 이미지를 Gemini AI로 번역

    Args:
        image_path: 원본 이미지 경로
        output_path: 결과 이미지 저장 경로
        model_name: 사용할 Gemini 모델명
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        _emit("❌ GEMINI_API_KEY 환경변수가 필요합니다", log_callback)
        return False

    client = genai.Client(api_key=api_key, http_options={'timeout': 300})

    _emit(f"📖 표지 번역 시작: {Path(image_path).name}", log_callback)
    _emit(f"🤖 모델: {model_name}", log_callback)

    # 이미지 업로드
    _emit(f"  📤 이미지 업로드 중...", log_callback)
    try:
        image_file = client.files.upload(file=image_path)
    except Exception as e:
        _emit(f"❌ 이미지 업로드 실패: {e}", log_callback)
        return False

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
    _emit(f"  🔄 Gemini AI 처리 중...", log_callback)

    max_retries = 3
    for attempt in range(max_retries):
        try:
            if attempt > 0:
                _emit(f"     재시도 {attempt}/{max_retries-1}...", log_callback)
                time.sleep(5)

            response = client.models.generate_content(
                model=model_name,
                contents=[prompt, image_file],
                config=types.GenerateContentConfig(
                    temperature=0.4,
                    top_p=0.95,
                    top_k=40,
                    max_output_tokens=8192,
                    response_modalities=["Image", "Text"],
                )
            )

            # 성공 - 응답 처리
            if not response or not response.candidates:
                if attempt < max_retries - 1:
                    _emit(f"     ⚠️  빈 응답, 재시도 중...", log_callback)
                    continue
                _emit("❌ Gemini 응답이 비어있습니다", log_callback)
                return False

            # 응답에서 이미지 추출
            parts = response.candidates[0].content.parts or []
            for part in parts:
                try:
                    if hasattr(part, 'inline_data') and part.inline_data:
                        mime_type = getattr(part.inline_data, 'mime_type', 'unknown')
                        _emit(f"  ℹ️ 파트 타입: {mime_type}", log_callback)

                        if mime_type and 'image' in mime_type:
                            image_data = part.inline_data.data
                            if isinstance(image_data, bytes) and len(image_data) > 100:
                                with open(output_path, 'wb') as f:
                                    f.write(image_data)
                                _emit(f"\n✅ 번역 완료: {output_path}", log_callback)
                                return True
                except Exception as part_error:
                    _emit(f"  ℹ️ 파트 처리 오류: {str(part_error)[:100]}", log_callback)
                    continue

            # 텍스트 응답 확인
            if response.text:
                _emit(f"\n📝 Gemini 응답 (텍스트):", log_callback)
                _emit(response.text[:500], log_callback)
                guide_path = Path(output_path).with_suffix('.txt')
                with open(guide_path, 'w', encoding='utf-8') as f:
                    f.write("표지 번역 결과:\n\n")
                    f.write(response.text)
                _emit(f"  📄 번역 가이드 저장: {guide_path}", log_callback)
                return False

            _emit("\n⚠️  응답에 이미지나 텍스트가 없습니다.", log_callback)
            return False

        except Exception as e:
            error_msg = str(e)
            if attempt < max_retries - 1:
                _emit(f"     ⚠️  오류: {error_msg[:100]}, 재시도 중...", log_callback)
                continue
            _emit(f"❌ 오류 발생: {error_msg}", log_callback)
            return False

    return False

def main():
    if len(sys.argv) < 2:
        print("사용법: python3 gemini_image_redraw.py <image_path> [output_path]")
        print("\n예시.")
        print("  python3 gemini_image_redraw.py cover.jpg")
        print("  python3 gemini_image_redraw.py cover.jpg output/cover_ko.jpg")
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
    success = redraw_image(image_path, output_path)

    if success:
        print("\n🎉 표지 번역 완료!")
    else:
        print("\n⚠️  이미지 생성 실패 - 텍스트 가이드를 참고하세요")

if __name__ == "__main__":
    main()
