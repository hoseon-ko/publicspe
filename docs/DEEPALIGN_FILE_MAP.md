# DeepAlign 파일 구조 정리

이 문서는 현재 DeepAlign 리팩터링 이후 파일 분리를 한눈에 보기 위한 문서입니다.
빠르게 아래 두 가지를 파악하는 용도입니다.

- 어떤 파일이 어떤 책임을 가지는가?
- 어떤 파일이 기능 파일이고 어떤 파일이 공통 지원 파일인가?

## 현재 DeepAlign 파일 목록

| 파일 | 주요 책임 | 비고 |
| --- | --- | --- |
| `ui/deepalign/deepalign_main_tab.py` | DeepAlign 탭의 구성 루트 | 5개 내부 페이지 스택을 만들고, 공통 상태를 들고 있으며, mixin과 외부 패널을 묶음 |
| `ui/deepalign/deepalign_layout.py` | DeepAlign 전용 UI/페이지 생성 | 카메라 페이지, 분석 페이지, 도킹 작업영역, 마스터 바, 사이드바 생성 |
| `ui/deepalign/deepalign_camera_controller.py` | 카메라 동작 및 획득 흐름 제어 | live, snap, acquire, exposure/FPS/temp/ADC 적용 로직 담당 |
| `ui/deepalign/deepalign_camera_hub_mixin.py` | hub 및 LiveTab 연동 보조 | scan/connect/disconnect, vendor 동기화, 카메라 목록 동기화 담당 |
| `ui/deepalign/deepalign_frame_pipeline.py` | 프레임 표시 파이프라인 및 ROI 동기화 | raw 프레임을 표시용 RGB로 바꾸고 viewer/ROI/범위/컬러맵 동기화 담당 |
| `ui/deepalign/deepalign_styles.py` | DeepAlign 공통 스타일 보조 | 버튼, 섹션, 진행률, 대시보드 라벨 등의 공통 UI 스타일 담당 |
| `ui/deepalign/deepalign_workers.py` | 백그라운드 워커 | SNAP, LIVE, ACQUIRE용 워커 객체 제공 |
| `ui/deepalign/deepalign_timing.py` | 획득 시간/진행률 계산 보조 | elapsed, progress ratio, ETA 포맷 계산용 순수 함수 모음 |
| `ui/deepalign/deep_align_main.py` | 기존 import 호환용 래퍼 | 예전 import를 유지하면서 새 canonical 모듈로 전달 |

## 이 분리를 읽는 방법

### 기능 소유 파일

아래 파일들은 DeepAlign의 실제 동작을 직접 표현하는 파일입니다.

- `deepalign_main_tab.py`
- `deepalign_layout.py`
- `deepalign_camera_controller.py`
- `deepalign_camera_hub_mixin.py`
- `deepalign_frame_pipeline.py`

### 공통 지원 파일

아래 파일들은 DeepAlign 내부에서 재사용되는 공통 보조 코드입니다.

- `deepalign_styles.py`
- `deepalign_workers.py`
- `deepalign_timing.py`

## 실전에서 보는 기준

DeepAlign를 수정할 때는 아래 기준으로 시작하면 됩니다.

- 사용자가 누를 수 있는 동작이나 상태 흐름이 바뀌면 `deepalign_main_tab.py`, `deepalign_layout.py`, `deepalign_camera_controller.py`를 먼저 봅니다.
- 카메라 연결 방식이나 session hub 연동이 바뀌면 `deepalign_camera_hub_mixin.py`를 봅니다.
- 프레임 표시 방식이나 ROI/컬러맵/범위 동기화가 바뀌면 `deepalign_frame_pipeline.py`를 봅니다.
- 화면 모양만 바뀌면 `deepalign_styles.py`를 봅니다.
- 스레드/백그라운드 실행이 바뀌면 `deepalign_workers.py`를 봅니다.
- 시간/진행률 계산만 바뀌면 `deepalign_timing.py`를 봅니다.

## 앞으로의 폴더 방향

나중에 DeepAlign 내부를 탭 기준으로 더 쪼개게 되면 자연스러운 목적지는 아래와 같습니다.

- `ui/deepalign/camera/`
- `ui/deepalign/mirror/`
- `ui/deepalign/focus/`
- `ui/deepalign/align/`
- `ui/deepalign/analysis/`
- `ui/deepalign/shared/`

그 전까지는 현재 파일명만으로도 책임이 꽤 잘 드러나므로, 수정 범위를 국소적으로 따라가기에는 충분합니다.
