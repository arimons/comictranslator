# 일괄 처리 배치의 메모리 증가 조사 메모 (2026-10-31)

`scripts/series_batch.py`로 초전자포 여러 권을 이어서 처리하는 동안 배치 프로세스의 메모리 점유가 계속 늘었다. 원인은 아직 모른다. 이 문서는 **확인한 사실, 가설, 아직 확인하지 못한 것**을 나눠 적은 조사 메모이고, 고치지는 않았다.

## 현상

- 맥미니(RAM 16GB)에서 한 권(약 220장)을 한 프로세스로 처리하면 점유량이 8GB를 넘고 스왑이 12GB까지 찼다. 프로세스를 새로 시작하면 줄어든다.
- 현재 회피책: `/tmp/run_volumes3.sh` 드라이버가 같은 권을 40장마다 새 프로세스로 이어서 돌린다(`series_batch.py --max-pages 40`, 끝날 때 `남은 대상 N장` 출력).

## 측정값 (`footprint <pid>` = 압축·스왑 포함 실제 점유량, `ps`의 RSS가 아니다)

| 시점 | 값 |
|---|---|
| v19 배치 프로세스(경과 24분, 약 120장 처리) | footprint 8,040MB, 압축 6,254MB, RSS 528MB |
| 같은 프로세스 75초 뒤(4장 처리) | 8,538MB (+318MB) |
| 위 시점의 구성 | `MALLOC_SMALL` 6,265MB(1,573 영역), `IOAccelerator`(GPU) 1,289MB, `MALLOC_LARGE` 339MB |
| v17 청크의 새 프로세스, 2분 39초(약 9장) | 3,447MB |
| 같은 프로세스 11분 29초(약 27장) | 5,511MB |

`ps`의 RSS만 보면 800MB 안팎이라 문제가 없어 보인다. 대부분이 압축·스왑으로 밀려난 상태여서 `footprint`나 활동 모니터로 봐야 보인다.

## 분리 측정 (`scripts/probe_step3_memory.py`)

분석 캐시가 있는 v20 이미지 8장으로 임시 프로젝트를 만들고, 같은 프로세스에서 페이지마다 `/api/analyze`와 `/api/guidance`(빈 가이던스)를 반복 호출했다. API 비용은 없다.

- 앱을 올린 직후 footprint 68MB.
- `/api/guidance` 한 번(이번 호출의 증가분): 1번째 +95MB, 2번째 +215MB, 3번째 +798MB.
- 같은 시점 tracemalloc이 추적한 파이썬 객체 메모리는 4MB, 5MB, 57MB였다.
- 즉 늘어난 것은 대부분 **파이썬 객체가 아니라 네이티브 메모리**(OpenCV·numpy·torch)다.
- 4번째 페이지의 `/api/analyze`에서 Step 3 서브프로세스가 시간 초과로 멈춰 측정이 중단됐다. 같은 시간대에 v17 배치가 돌고 있어 자원을 다툰 것으로 보이지만 확인하지는 않았다.

## 코드에서 확인한 사실

- 배치는 `TestClient(comic_app.create_app())`로 앱을 **같은 프로세스에** 올리고, 페이지마다 `/api/analyze`, `/api/guidance`, `/api/restore/accept`, `/api/run-final`을 호출한다(스레드 3개).
- `/api/analyze`는 Step 1〜3을 서브프로세스로 돌린다(`run_single_image`, `stop_after="pre_ocr"`).
- `/api/guidance`는 `run_step3_with_effective_text_groups`(`app.py` 2124행 부근)에서 `from pipeline.dialogue_extractor import build_dialogue_sheet`를 불러 Step 3 본체를 **프로세스 안에서 직접** 실행한다. 서브프로세스와 달리 끝나도 메모리가 운영체제로 돌아가지 않는다.
- `/api/run-final`의 Step 4는 서브프로세스(`pipeline.gemini_ocr_translate_v5`, 각 약 100MB)다. 렌더(`render_overlay_for_record`)가 프로세스 안에서 도는지는 확인하지 않았다.
- 페이지 이미지를 쌓아두는 캐시는 찾지 못했다. `_read_bgr`는 읽어서 돌려줄 뿐이다. 다만 이것은 `lru_cache`와 모듈 전역 딕셔너리를 찾은 수준의 확인이다.
- 전역 상태 후보: `app.py`의 `_JSON_FLAGS_CACHE`(1533행, 항목 2만 개 초과 시 비움), `_RESTORE_JOBS`(2816행, 항목이 지워지지 않음), `pipeline/dialogue_extractor.py`의 `_DETECTION_CACHE`(3098행, 상한 6), `pipeline/source_library.py`의 `_HASHES`.

## 가설 (모두 미확인)

1. Step 3 본체가 큰 배열을 반복 할당하고, macOS의 malloc이 작은 조각을 운영체제에 돌려주지 않아 점유가 계속 늘어난다(`MALLOC_SMALL` 6.3GB가 이 모양이다).
2. LaMa를 도는 torch(MPS)가 GPU 메모리를 캐시로 쥐고 있다(`IOAccelerator` 1.3GB). `torch.mps.empty_cache()` 호출이 있는지 확인하지 않았다.
3. `_DETECTION_CACHE`는 상한이 6이라 120장 뒤에도 늘어나는 것을 설명하지 못한다. 단독 원인은 아닐 가능성이 크다.

## 주의할 점

- "장당 약 100MB" 같은 증가율은 거친 추정이다. 위 측정에는 torch·LaMa 모델을 처음 올리는 비용(가중치 196MB 등)과 페이지마다 다른 작업량이 섞여 있어서, 호출마다 일정하게 늘어나는지는 모른다. 3번째 호출의 +798MB는 한 번 크게 뛴 것이다.
- 8GB가 장수에 비례해 늘었는지, 몇 번의 큰 도약 뒤에 정체하는지는 구분하지 못했다. 곡선을 먼저 그려야 한다.

## 이어서 할 일 (제안)

1. 조용한 상태에서 `probe_step3_memory.py`를 20장 이상으로 돌려 호출별 증가 곡선을 그린다(워밍업 구간 분리).
2. `/api/guidance` 호출을 서브프로세스로 돌리게 바꿔 같은 측정을 하고 비교한다.
3. 각 페이지 뒤 `gc.collect()`와 `torch.mps.empty_cache()`를 부르면 점유가 줄어드는지 본다.
4. `vmmap`, `leaks`, `MallocStackLogging`으로 `MALLOC_SMALL`의 할당 위치를 본다.

## 추가 측정: LaMa 입력 크기 (2026-10-31, `scripts/probe_lama_shape_memory.py`)

LaMa만 떼어 같은 프로세스에서 임의 이미지로 반복 호출했다. API 비용은 없다. 값은 footprint(MB), 모델 로드 직후 약 486MB.

| 조건 (MPS) | 10회 | 40회 | 60회 | 120회 |
|---|---|---|---|---|
| 크기 고정 320×320 | 1,680 | 1,680 | 1,681 | - |
| 매번 다른 크기(100-700px) | 2,066 | 3,998 | 4,474 | 6,206 |
| 다른 크기 + 매회 `gc.collect()`·`torch.mps.empty_cache()` | 2,021 | 1,884 | 2,361 | - |
| 128px 배수로 올림(크기 종류 36개) | 2,158 | 2,328 | 3,770 | 4,839 |
| 128px 배수 + `empty_cache()` | 2,144 | 2,289 | 2,250 | 2,819 |

- 같은 크기만 넣으면 늘지 않는다. **새 입력 크기가 나올 때마다 점유가 늘고 돌아오지 않는다.** TorchScript/MPS가 크기별 실행 그래프와 버퍼를 쌓는 모양과 맞는다.
- 실제 Step 3은 ROI마다 크기가 다르므로 페이지가 쌓일수록 새 크기가 계속 나온다. 가설 1·2는 이 현상의 일부로 보인다.
- CPU(`DEV=cpu`)는 계속 오르지는 않았지만 2,000-7,400MB 사이로 크게 출렁여 대안이 되지 못했다.
- `/api/analyze`의 Step 3는 서브프로세스라 끝나면 풀린다. 누적되는 쪽은 프로세스 안에서 LaMa를 쓰는 `/api/guidance`(`run_step3_with_effective_text_groups`)와 배경 복원 작업이다.
- 아직 확인하지 않은 것: 실제 배치에 크기 올림 + `empty_cache()`를 넣었을 때 220장 기준 곡선, 크기 올림 패딩이 복원 결과를 바꾸는 정도.

## 재현

```bash
ssh chang-hyuns-macmini 'cd ~/Developer/NEW_COMIC_TRANSLATOR && ulimit -n 4096 && .venv/bin/python scripts/probe_step3_memory.py'
```

프로세스의 실제 점유량은 아래로 본다.

```bash
ssh chang-hyuns-macmini 'footprint <pid> | head -12'
```
