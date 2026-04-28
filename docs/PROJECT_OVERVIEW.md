# SpeAnalyze — 통합 실험실 제어 앱 개요

> **대상 독자:** 이 프로젝트에 처음 합류한 신입 연구원 / 개발자  
> **목적:** 코드를 처음 보는 사람도 "무엇을, 왜, 어떻게" 만들었는지 이해할 수 있도록 정리

---

## 1. 이 프로그램이 왜 필요한가?

AMMI 연구실에서는 **레이저 빔 이미징 실험**을 수행한다.  
실험의 핵심 작업 3가지:

| 작업 | 기존 방식의 문제 | 이 앱의 해결 |
|------|----------------|------------|
| 카메라로 빔 촬영 | 제조사 소프트웨어가 각각 달라 통합 불가 | HIKVISION / Picam 두 카메라를 하나의 UI로 제어 |
| 피코모터로 광학계 정렬 | 별도 콘솔 프로그램 사용, 카메라와 동시 못 봄 | 카메라 영상 보면서 실시간 모터 제어 |
| 분광 데이터 분석 | MATLAB / Python 스크립트 따로 실행 | SPE 파일 로드 → 프로파일 / 히스토그램 즉시 분석 |

한 마디로: **카메라 + 모터 + 데이터 분석을 하나의 창에서 전부 처리**하는 실험실 전용 GUI 앱이다.

---

## 2. 전체 앱 구조 (3-탭 구성)

```
┌─────────────────────────────────────────────────────────┐
│  SpeAnalyze — Integrated Lab Control                    │
├──────────────┬──────────────┬───────────────────────────┤
│ 📷 LIVE      │ 🔬 ACQUISITION│ 📊 SPE ANALYSIS           │
│ CONTROL      │              │                           │
└──────────────┴──────────────┴───────────────────────────┘
```

### Tab 1: LIVE CONTROL (실시간 제어)
- 카메라(HIKVISION 또는 Picam)로 **실시간 영상** 스트리밍
- 영상 위에 **이진화 / 중심 좌표(Centroid) / 로그 스케일** 처리 실시간 적용
- **Picomotor 4축** 동시 제어 (전진/후진, 가중치 보정 포함)
- Line / Box / Histogram ROI 드래그 → **프로파일 / 히스토그램** 즉시 표시

### Tab 2: ACQUISITION (배치 획득)
- **Picam 전용** — N 프레임 장노출 배치 획득
- 온도 제어(냉각 CCD) / ADC 설정 가능
- 획득 완료 → **SPE 3.0 파일로 자동 저장** → Analysis 탭 자동 오픈

### Tab 3: SPE ANALYSIS (데이터 분석)
- Princeton Instruments / Teledyne LightField의 `.spe` 파일 로드
- 프레임 그리드 썸네일 / 프로파일 / 히스토그램 / ROI 분석
- 여러 파일 동시 비교 가능

---

## 3. 폴더 구조 완전 해설

```
SpeAnalyze/
│
├── main.py                     ← 앱 진입점. QApplication 생성 후 MainWindow 실행
├── spe_reader.py               ← SPE 2.x / 3.0 바이너리 파일 파서 (SpeFile 클래스)
├── picamp.py                   ← Picam SDK 래퍼 (pylablib 기반, 고수준 API 제공)
│
├── core/                       ← UI 없는 순수 로직 (하드웨어 추상화 + 연산)
│   ├── image_processor.py      ← 카메라 무관 이미지 후처리 파이프라인
│   ├── spe_writer.py           ← 모든 카메라 → SPE 3.0 저장 (통일된 저장 함수)
│   ├── camera/
│   │   ├── base.py             ← BaseCamera 추상 클래스 + CameraCapabilities
│   │   ├── hikvision.py        ← HIKVISION MVS SDK 래퍼
│   │   └── picam_cam.py        ← Picam SDK 래퍼 (picamp.py 재포장)
│   └── motor/
│       └── picomotor.py        ← Newport Picomotor 8742 제어 + 폴링 워커
│
├── ui/                         ← 모든 GUI 위젯
│   ├── main_window.py          ← 3-탭 MainWindow (탭 연결 허브)
│   ├── image_viewer.py         ← 공용 이미지 뷰어 (ROI, 크로스헤어, 눈금자)
│   ├── plot_panel.py           ← PlotPanel(프로파일) + HistogramPanel
│   ├── roi_items.py            ← LineROI / BoxROI / HistROI (드래그 가능한 그래픽 아이템)
│   ├── roi_panel.py            ← ROI 목록 패널 (선택/삭제)
│   ├── file_list_panel.py      ← SPE 파일 목록 위젯
│   ├── frame_grid_panel.py     ← 프레임 썸네일 그리드
│   ├── live/
│   │   ├── live_tab.py         ← Live Control 탭 (QMainWindow 기반 도킹)
│   │   ├── camera_panel.py     ← 카메라 연결/파라미터 제어 패널
│   │   └── motor_panel.py      ← Picomotor 4축 제어 패널 + 가중치
│   ├── acquisition/
│   │   └── acquisition_tab.py  ← Picam 배치 획득 탭
│   └── analysis/
│       └── analysis_tab.py     ← SPE 분석 탭 (QMainWindow 기반 도킹)
│
└── theme/
    └── dark_theme.py           ← 전체 앱 다크 테마 QSS (Qt Style Sheet)
```

---

## 4. 핵심 아키텍처 패턴 (왜 이렇게 설계했나?)

### 4-1. Capability Pattern (기능 선언 패턴)

**문제:** HIKVISION과 Picam은 지원 기능이 완전히 다르다.  
HIKVISION은 FPS 제어 / 이진화를 지원하지만 온도 제어는 없다.  
Picam은 반대로 온도 제어 / ADC는 있지만 이진화는 없다.

**해결:** `CameraCapabilities` 데이터클래스로 기능 목록을 선언.  
UI는 이 목록을 읽어 **지원하는 기능만 표시**한다.

```python
# core/camera/base.py
@dataclass
class CameraCapabilities:
    has_fps_control: bool = False
    has_binarize:    bool = False
    has_temperature: bool = False
    has_adc:         bool = False
    ...

# ui/live/camera_panel.py
def attach_camera(self, cam):
    caps = cam.capabilities
    self.grp_fps.setVisible(caps.has_fps_control)   # HIKVISION만 보임
    self.grp_temp.setVisible(caps.has_temperature)  # Picam만 보임
```

### 4-2. 카메라 인스턴스 공유 (Live ↔ Acquisition)

**문제:** Picam SDK는 같은 카메라를 두 번 열면 충돌한다.

**해결:** `LiveTab`에서 카메라를 연결하면 `MainWindow`가 같은 인스턴스를  
`AcquisitionTab`에 전달한다. Acquisition 탭은 자체 연결/해제를 하지 않는다.

```
LiveTab (카메라 연결)
    ↓ camera_connected(cam) 시그널
MainWindow
    ↓ set_shared_camera(cam) 호출
AcquisitionTab ── 같은 cam 인스턴스 사용
```

Acquisition 시작 시 `acquisition_starting` 시그널 → MainWindow → `LiveTab.stop_live()`  
**라이브 스트림 자동 정지 후 배치 획득 시작.**

### 4-3. 스레드 안전 프레임 처리

**문제:** 카메라 워커(별도 스레드)가 직접 Qt 위젯을 건드리면 크래시.

**해결:** `pyqtSignal`로 스레드 경계 분리.

```python
# 워커 스레드 (카메라 콜백)
def _on_frame(self, raw):
    result = self._proc.process(raw)   # 이미지 처리 (스레드 OK)
    self._frame_ready.emit(rgb)        # 시그널만 emit

# 메인 스레드 (슬롯)
def _show_frame(self, rgb):
    self.image_viewer.set_image(rgb)   # 위젯 업데이트 (메인 스레드 OK)
```

### 4-4. QMainWindow 도킹 (Live + Analysis 탭 공통)

`LiveTab`과 `AnalysisTab`은 모두 `QMainWindow`를 상속.  
`QMainWindow`만이 `QDockWidget`을 호스팅할 수 있기 때문.

`QTabWidget` 안에 임베드할 때 필수 설정:
```python
self.setWindowFlags(Qt.WindowType.Widget)  # 독립 창으로 뜨지 않게
self.menuBar().setVisible(False)           # 메뉴바 숨김
```

### 4-5. SPE 저장 통일

어떤 카메라든 `core/spe_writer.py`의 `save_spe()` 함수 하나로 저장.  
Analysis 탭은 저장된 SPE를 바로 열 수 있다 (`spe_saved` 시그널 연동).

---

## 5. 하드웨어 구성

```
┌─────────────────────────────────────────────┐
│               실험 셋업                       │
│                                             │
│  레이저 빔                                   │
│      ↓                                      │
│  광학 미러 (Picomotor로 정렬)                │
│      ↓                                      │
│  ┌──────────┐    ┌──────────┐              │
│  │HIKVISION │ OR │  Picam   │              │
│  │ 산업용   │    │  냉각 CCD│              │
│  │ 카메라   │    │  분광기  │              │
│  └──────────┘    └──────────┘              │
│       ↓                ↓                   │
│          USB / PCIe                        │
│               ↓                            │
│         이 앱 (PC)                          │
└─────────────────────────────────────────────┘
```

### 카메라별 특징

| | HIKVISION MV-CA | Picam (Princeton) |
|---|---|---|
| 용도 | 실시간 빔 모니터링 | 고감도 분광 이미징 |
| 연결 | USB / GigE | PCIe / USB |
| SDK | MVS (MvCameraControl_class) | pylablib (PrincetonInstruments) |
| 특이사항 | FPS 제어, 이진화 가능 | 온도 냉각(-70°C), ADC 설정 |

### Picomotor 8742
- Newport 사 4축 압전 모터 컨트롤러
- USB로 PC 연결
- 제어: `DeviceIOLib.dll` + `CmdLib8742.dll` (Windows DLL, pythonnet으로 호출)
- 1 step ≈ 30nm 분해능

---

## 6. 데이터 흐름 전체 그림

```
[카메라 워커 스레드]
  Raw Frame (numpy)
      ↓
  ImageProcessor.process()
  ├─ N프레임 평균화
  ├─ 배경 차분
  ├─ 로그 스케일
  ├─ 이진화
  └─ Centroid 계산
      ↓
  _frame_ready 시그널 (스레드 경계)
      ↓
[GUI 메인 스레드]
  ImageViewer.set_image()
  ├─ 컬러맵 적용 (jet/viridis/hot/...)
  ├─ 화면 표시
  └─ ROI 활성화 시:
      ├─ Line Profile → PlotPanel
      ├─ Box Profile  → PlotPanel
      └─ Histogram    → HistogramPanel

[저장 경로]
  SAVE 버튼 클릭
  ├─ BMP (Raw + Display)
  └─ CSV (Timestamp, Centroid, Motor Positions)

[Acquisition 경로]
  N프레임 배치 획득 (_AcqWorker 스레드)
      ↓
  SPE 3.0 저장 (core/spe_writer.py)
      ↓
  spe_saved 시그널 → Analysis 탭 자동 오픈
```

---

## 7. 주요 시그널 연결 지도

> Qt의 시그널-슬롯은 "이벤트가 발생하면 이 함수를 호출해라"는 연결이다.

```
MainWindow
  ├─ live_tab.camera_connected    → acq_tab.set_shared_camera
  ├─ live_tab.camera_disconnected → acq_tab.clear_shared_camera
  ├─ acq_tab.acquisition_starting → live_tab.stop_live
  ├─ acq_tab.spe_saved            → analysis_tab.open_spe + 탭 전환
  └─ live_tab.status_message      → 하단 상태바 갱신

LiveTab (내부)
  ├─ cam_panel.camera_scan_requested    → _scan_cameras
  ├─ cam_panel.camera_connect_requested → _connect_camera
  ├─ image_viewer.line_profile_updated  → plot_panel.plot_line
  ├─ image_viewer.histogram_updated     → hist_panel.plot_histogram
  └─ motor_panel.log_message            → _log

MotorPanel (내부)
  └─ MotorCard._on_move_requested → PicomotorController.move_relative
                                    (가중치 적용 후)
```

---

## 8. 파일별 한 줄 요약

| 파일 | 한 줄 요약 |
|------|-----------|
| `main.py` | 앱 시작점. QApplication + MainWindow |
| `spe_reader.py` | SPE 바이너리 → numpy 배열 변환 |
| `picamp.py` | Picam SDK 고수준 래퍼 |
| `core/image_processor.py` | 이미지 처리 파이프라인 (카메라 무관) |
| `core/spe_writer.py` | SPE 3.0 포맷 저장 |
| `core/camera/base.py` | 카메라 추상 인터페이스 정의 |
| `core/camera/hikvision.py` | HIKVISION 카메라 구체 구현 |
| `core/camera/picam_cam.py` | Picam 카메라 구체 구현 |
| `core/motor/picomotor.py` | Picomotor DLL 제어 + 300ms 폴링 |
| `ui/main_window.py` | 3탭 통합 창 + 탭간 시그널 연결 |
| `ui/image_viewer.py` | 줌/스크롤/ROI/크로스헤어/컬러맵 뷰어 |
| `ui/plot_panel.py` | 프로파일 플롯 + 히스토그램 |
| `ui/live/live_tab.py` | Live 탭 (QMainWindow, 도킹) |
| `ui/live/camera_panel.py` | Capability 기반 동적 카메라 UI |
| `ui/live/motor_panel.py` | 4축 모터 제어 + 가중치 |
| `ui/acquisition/acquisition_tab.py` | Picam 배치 획득 탭 |
| `ui/analysis/analysis_tab.py` | SPE 분석 탭 (QMainWindow, 도킹) |

---

## 9. 처음 코드를 읽을 때 권장 순서

```
1. main.py                    ← 앱이 어떻게 시작되는지
2. ui/main_window.py          ← 탭 구조와 시그널 연결
3. core/camera/base.py        ← 카메라 추상화 이해
4. core/image_processor.py    ← 이미지 파이프라인 이해
5. ui/live/live_tab.py        ← Live 탭 전체 흐름
6. ui/live/camera_panel.py    ← Capability 패턴 실제 적용
7. ui/live/motor_panel.py     ← 모터 제어 + 가중치
8. ui/acquisition/acquisition_tab.py  ← 배치 획득 흐름
9. ui/analysis/analysis_tab.py        ← 분석 탭
10. core/camera/hikvision.py  ← 실제 하드웨어 연결
```

---

## 10. 자주 나오는 약어 / 용어

| 용어 | 설명 |
|------|------|
| **SPE** | Princeton Instruments 독점 이미지 포맷 (`.spe`) |
| **Centroid** | 밝기 분포의 무게중심 좌표 (빔 위치 추적에 사용) |
| **ROI** | Region of Interest — 관심 영역 (선/박스 드래그) |
| **ADC** | Analog-to-Digital Converter — Picam의 변환 품질 설정 |
| **Picomotor** | Newport의 압전 소자 기반 정밀 스테핑 모터 |
| **QDockWidget** | Qt에서 분리/이동 가능한 패널 위젯 |
| **pyqtSignal** | Qt의 이벤트-콜백 연결 메커니즘 |
| **Worker Thread** | 카메라/모터 I/O를 메인 UI 스레드와 분리해서 실행하는 백그라운드 스레드 |
| **Capability Pattern** | 기능 목록을 데이터로 선언해 UI가 동적으로 표시/숨김하는 패턴 |
