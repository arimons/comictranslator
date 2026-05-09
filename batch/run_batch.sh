#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TARGET_BASE="input/Toaru_Kagaku_no_Choudenjihou"

cd "$PROJECT_ROOT" || exit 1

for dir in "$TARGET_BASE"/*; do
    if [ -d "$dir" ]; then
        echo "=========================================================="
        echo "▶️ [시작] 폴더 전송: $dir"
        echo "=========================================================="
        python3 batch/submit_batch_folder.py "$dir"
        echo "⏳ [대기] 다음 폴더 발송 전 안전하게 10초 대기 중..."
        sleep 10
    fi
done
echo "🎉 모든 폴더의 배치가 접수되었습니다!"
