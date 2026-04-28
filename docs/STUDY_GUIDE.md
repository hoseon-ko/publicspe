# SpeAnalyze 개발을 위한 학습 가이드

> **대상:** 이 프로젝트에 기여하거나 유지보수하려는 신입 개발자  
> **목표:** 이 앱을 이해하고 수정하는 데 필요한 지식과 학습 순서 안내

---

## 학습 로드맵 (추천 순서)

```
[ 1단계 ]  Python 기초
    ↓
[ 2단계 ]  numpy + 이미지 처리 기초
    ↓
[ 3단계 ]  PyQt6 (GUI 프레임워크)
    ↓
[ 4단계 ]  pyqtgraph (실시간 플롯)
    ↓
[ 5단계 ]  opencv-python (이미지 처리)
    ↓
[ 6단계 ]  하드웨어 SDK (카메라 / 모터)
    ↓
[ 7단계 ]  SPE 파일 포맷 이해
```

---

## 1단계: Python 기초

이 프로젝트는 **Python 3.10+** 기준으로 작성되어 있다.  
아래 개념들은 코드 전반에 걸쳐 등장한다.

### 필수 개념

| 개념 | 코드에서 쓰이는 곳 |
|------|-----------------|
| 클래스 / 상속 | `BaseCamera` → `HikvisionCamera`, `PicamCamera` |
| `dataclass` | `CameraCapabilities`, `ProcessedFrame` |
| 추상 클래스 (`ABC`, `abstractmethod`) | `BaseCamera`의 `connect()`, `snap()` 등 |
| `Optional`, `List`, `Dict` (타입 힌트) | 모든 함수 시그니처 |
| `try / except` | 하드웨어 연결 실패 처리 |
| `threading.Event` | Picam Live 워커 정지 신호 |
| `collections.deque` | 프레임 평균화 버퍼 |
| `dataclasses.field` | 기본값 있는 데이터클래스 필드 |
| `__from__future__ import annotations` | 순환 임포트 방지 |

### 학습 자료
- 공식 튜토리얼: https://docs.python.org/ko/3/tutorial/
- 타입 힌트: https://mypy.readthedocs.io/en/stable/cheat_sheet_py3.html
- dataclass: https://docs.python.org/ko/3/library/dataclasses.html

---

## 2단계: numpy

**역할:** 카메라 프레임 = numpy 2D 배열. 모든 이미지 연산의 기반.

### 핵심 사용 패턴

```python
import numpy as np

# 카메라 프레임은 이런 형태
frame = np.zeros((1024, 1280), dtype=np.uint8)  # 그레이스케일
frame = np.zeros((1024, 1280, 3), dtype=np.uint8)  # RGB

# 정규화 (0~1 범위로)
f = frame.astype(np.float64)
f = (f - f.min()) / (f.max() - f.min())

# 슬라이싱 (ROI 영역 추출)
region = frame[y0:y1, x0:x1]

# 통계
mean_val = region.mean()
histogram, edges = np.histogram(region.flatten(), bins=256)

# 스택 (그레이 → RGB)
rgb = np.stack([gray, gray, gray], axis=-1)

# 클립
clipped = np.clip(data, 0, 255).astype(np.uint8)
```

### 학습 자료
- numpy 공식: https://numpy.org/doc/stable/user/quickstart.html
- 이미지 처리용 numpy: https://numpy.org/doc/stable/reference/routines.array-manipulation.html

---

## 3단계: PyQt6 ⭐ (가장 중요)

**역할:** 이 앱 전체의 GUI 프레임워크. 가장 많은 시간을 투자해야 한다.

### PyQt6 핵심 개념 4가지

#### 3-1. 위젯 (Widget)
모든 UI 요소의 기본 단위.

```python
from PyQt6.QtWidgets import (
    QWidget, QMainWindow,       # 창
    QLabel, QPushButton,        # 기본 위젯
    QVBoxLayout, QHBoxLayout,   # 레이아웃
    QGroupBox, QDockWidget,     # 컨테이너
    QSpinBox, QDoubleSpinBox,   # 숫자 입력
    QComboBox, QCheckBox,       # 선택
    QTextEdit, QScrollArea,     # 텍스트/스크롤
    QTabWidget, QSplitter,      # 탭/분할
)
```

#### 3-2. 시그널-슬롯 (Signal-Slot) ⭐⭐⭐

Qt의 이벤트 시스템. **이것만 확실히 이해하면 코드의 70%가 읽힌다.**

```python
from PyQt6.QtCore import pyqtSignal, QObject

class MyWorker(QObject):
    # 시그널 선언 (클래스 레벨)
    data_ready = pyqtSignal(np.ndarray)   # numpy 배열 전달
    finished   = pyqtSignal()
    error      = pyqtSignal(str)

    def run(self):
        try:
            frame = camera.grab()
            self.data_ready.emit(frame)   # 시그널 발사
            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))

class MyWindow(QWidget):
    def __init__(self):
        super().__init__()
        worker = MyWorker()
        worker.data_ready.connect(self.on_frame)   # 연결
        worker.error.connect(lambda msg: print(msg))

    def on_frame(self, frame):
        self.image_viewer.set_image(frame)   # 슬롯
```

**왜 중요한가?**
- 버튼 클릭 → 함수 호출 (단순한 경우)
- 스레드 → GUI 업데이트 (스레드 안전하게)
- 탭 간 데이터 전달 (이 앱의 핵심 구조)

#### 3-3. QThread + Worker 패턴

카메라나 모터는 느리다. 메인 스레드에서 기다리면 UI가 멈춘다.  
**반드시 별도 스레드에서 실행해야 한다.**

```python
from PyQt6.QtCore import QThread, QObject, pyqtSignal

class Worker(QObject):          # QThread가 아닌 QObject를 상속!
    finished = pyqtSignal()
    
    def run(self):              # 실제 작업
        do_heavy_work()
        self.finished.emit()

# 사용
thread = QThread()
worker = Worker()
worker.moveToThread(thread)     # 워커를 스레드로 이동
thread.started.connect(worker.run)
worker.finished.connect(thread.quit)
thread.start()
```

> ⚠️ **주의:** `worker.run()` 안에서 Qt 위젯을 직접 건드리면 안 된다.  
> 반드시 시그널로 메인 스레드에 전달해야 한다.

#### 3-4. QDockWidget (도킹 패널)

이 앱의 Live 탭과 Analysis 탭에서 사용하는 분리/이동 가능한 패널.  
**`QMainWindow`에서만 사용 가능하다.**

```python
from PyQt6.QtWidgets import QMainWindow, QDockWidget
from PyQt6.QtCore import Qt

class MyTab(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.Widget)  # 탭에 임베드할 때 필수
        self.menuBar().setVisible(False)

        # 중앙 위젯
        self.setCentralWidget(ImageViewer())

        # 도킹 패널 추가
        dock = QDockWidget("설정", self)
        dock.setWidget(SettingsPanel())
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)
```

### PyQt6 학습 자료
- 공식 문서: https://doc.qt.io/qtforpython-6/
- 튜토리얼 (한국어): https://wikidocs.net/book/2944
- 시그널-슬롯 심화: https://doc.qt.io/qt-6/signalsandslots.html

### 이 앱에서 자주 쓰는 Qt 클래스 목록

```
QWidget         — 모든 UI 요소의 부모
QMainWindow     — 메뉴바/툴바/도킹 지원 창
QDockWidget     — 분리 가능한 패널
QTabWidget      — 탭 컨테이너
QScrollArea     — 스크롤 가능한 영역
QGroupBox       — 테두리+제목 있는 그룹
QVBoxLayout     — 세로 배치
QHBoxLayout     — 가로 배치
QGridLayout     — 격자 배치
QSplitter       — 크기 조절 가능한 분할
QLabel          — 텍스트/이미지 표시
QPushButton     — 버튼
QComboBox       — 드롭다운 선택
QCheckBox       — 체크박스
QSpinBox        — 정수 입력
QDoubleSpinBox  — 소수 입력
QSlider         — 슬라이더
QTextEdit       — 멀티라인 텍스트
QListWidget     — 목록
QProgressBar    — 진행바
QTimer          — 주기적 함수 호출
QThread         — 백그라운드 스레드
QFileDialog     — 파일 선택 다이얼로그
pyqtSignal      — 커스텀 시그널 선언
```

---

## 4단계: pyqtgraph

**역할:** 실시간 과학 데이터 플롯. matplotlib보다 훨씬 빠르다 (GPU 가속).  
이 앱의 Profile Plot과 Histogram에 사용된다.

```python
import pyqtgraph as pg

# 기본 플롯 위젯
plot_widget = pg.PlotWidget()
plot_widget.setBackground('#16213e')
plot_widget.showGrid(x=True, y=True, alpha=0.3)

# 선 그리기
plot_widget.plot(x_data, y_data, pen=pg.mkPen('#e94560', width=1.5), name="Line")

# 히스토그램 (막대 그래프)
bar = pg.BarGraphItem(x=bin_edges[:-1], height=counts, width=bin_width, brush='#4ecdc4')
plot_widget.addItem(bar)

# 축 레이블
plot_widget.setLabel('bottom', 'Pixel')
plot_widget.setLabel('left', 'Intensity')

# 범례
plot_widget.addLegend()

# 실시간 업데이트 (기존 아이템 교체)
curve = plot_widget.plot(pen='r')
curve.setData(new_x, new_y)  # 매 프레임마다 호출
```

### 학습 자료
- 공식 예제: https://pyqtgraph.readthedocs.io/en/latest/getting_started/examples.html
- API 문서: https://pyqtgraph.readthedocs.io/en/latest/api_reference/

---

## 5단계: opencv-python (cv2)

**역할:** 이미지 처리 연산 (이진화, centroid, 컬러 변환, 파일 저장).

```python
import cv2
import numpy as np

# 그레이스케일 → RGB
rgb = cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB)

# 이진화 (threshold 이상 → 255)
_, binary = cv2.threshold(gray, 127, 255, cv2.THRESH_BINARY)

# Centroid (무게중심) 계산
moments = cv2.moments(binary)
if moments['m00'] != 0:
    cx = moments['m10'] / moments['m00']
    cy = moments['m01'] / moments['m00']

# 마커 그리기
cv2.drawMarker(rgb, (int(cx), int(cy)), (78, 205, 196), cv2.MARKER_CROSS, 40, 2)

# 텍스트 그리기
cv2.putText(rgb, f"({cx:.1f}, {cy:.1f})", (x+10, y-10),
            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (233, 69, 96), 1)

# 이미지 저장
cv2.imwrite("output.bmp", frame)

# 로그 스케일
log_img = np.log1p(frame.astype(np.float64))
log_norm = cv2.normalize(log_img, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
```

### 학습 자료
- 공식 튜토리얼: https://docs.opencv.org/4.x/d6/d00/tutorial_py_root.html
- 한국어 자료: https://opencv-python.readthedocs.io/en/latest/

---

## 6단계: 하드웨어 SDK

### 6-1. HIKVISION MVS SDK

산업용 카메라 제어. Windows에 MVS SDK를 설치하면 Python 바인딩 포함.

```python
# MVS SDK는 pip로 설치하지 않는다
# MVS SDK 설치 후 MvCameraControl_class.py 사용

from MvCameraControl_class import *

deviceList = MV_CC_DEVICE_INFO_LIST()
MvCamera.MV_CC_EnumDevices(MV_GIGE_DEVICE | MV_USB_DEVICE, deviceList)

cam = MvCamera()
cam.MV_CC_CreateHandle(deviceList.pDeviceInfo[0])
cam.MV_CC_OpenDevice()
cam.MV_CC_StartGrabbing()

# 프레임 가져오기
stOutFrame = MV_FRAME_OUT()
cam.MV_CC_GetImageBuffer(stOutFrame, 1000)  # 1000ms timeout
# → stOutFrame.pBufAddr 에 픽셀 데이터
```

**설치:** https://www.hikrobotics.com/en/machinevision/service/download

### 6-2. Picam (pylablib)

Princeton Instruments / Teledyne LightField 카메라.  
`pylablib`가 SDK를 Python에서 쉽게 쓸 수 있게 래핑해줬다.

```python
from pylablib.devices import PrincetonInstruments as PI

cam = PI.PicamCamera()
cam.set_attribute_value("ExposureTime", 100.0)  # ms
cam.set_temperature(-70)                         # 냉각

frame = cam.snap()           # 단일 프레임
frames = cam.grab(10)        # 10프레임 배치
```

**설치:** `pip install pylablib`  
**주의:** Picam SDK (LightField)가 PC에 별도 설치되어 있어야 함.

### 6-3. Newport Picomotor 8742 (pythonnet)

Newport 사에서 제공하는 Windows DLL을 Python에서 호출.  
`pythonnet` (clr) 패키지로 .NET DLL을 Python에서 직접 사용.

```python
import clr
clr.AddReference(r"C:\Program Files\Newport\Picomotor\DeviceIOLib")
clr.AddReference(r"C:\Program Files\Newport\Picomotor\CmdLib8742")

from Newport.DeviceIOLib import DeviceIOLib
from Newport.CmdLib8742 import CmdLib8742

io = DeviceIOLib()
io.OpenDevices()

cmd = CmdLib8742(io)
cmd.RelativeMove(1, 100)   # Motor 1, +100 steps
pos = cmd.GetPosition(1)   # Motor 1 현재 위치
```

**설치:** `pip install pythonnet`  
**주의:** Newport Picomotor 드라이버가 설치되어 있어야 함.

---

## 7단계: SPE 파일 포맷

Princeton Instruments 독점 바이너리 포맷.  
`.spe` 파일 = **4100 바이트 헤더 + 프레임 데이터 + XML 메타데이터(v3.0)**

```
[파일 구조]
오프셋 0    ~ 4099 : 바이너리 헤더 (해상도, 노출시간, 데이터타입 등)
오프셋 4100 ~      : 프레임 데이터 (uint16 또는 float32 배열)
파일 끝              : XML 메타데이터 (v3.0만 해당)
```

이 앱에서는 `spe_reader.py`의 `SpeFile` 클래스로 읽고,  
`core/spe_writer.py`의 `save_spe()` 함수로 쓴다.

**참고 문서:** Teledyne LightField 개발자 문서 (사내 공유 드라이브 참조)

---

## 설치 방법

### 1. Python 환경 설정
```bash
# Python 3.10 이상 권장
python --version

# 가상환경 생성
python -m venv .venv
.venv\Scripts\activate  # Windows
```

### 2. Python 패키지 설치
```bash
pip install PyQt6 pyqtgraph numpy scipy opencv-python pylablib pythonnet
```

### 3. 외부 SDK 설치 (하드웨어 사용 시)
- **HIKVISION MVS SDK:** 연구실 NAS 또는 공식 사이트
- **Picam (LightField):** Teledyne 공식 사이트 (라이선스 필요)
- **Newport Picomotor:** Newport 공식 사이트

### 4. 실행
```bash
python main.py
```

---

## 빠른 참조: 에러 상황별 원인

| 에러 메시지 | 원인 | 해결 |
|------------|------|------|
| `ImportError: No module named 'MvCameraControl_class'` | MVS SDK 미설치 | MVS SDK 설치 + Python 경로 추가 |
| `ImportError: No module named 'pylablib'` | pylablib 미설치 | `pip install pylablib` |
| `System.IO.FileNotFoundException` (clr) | Picomotor DLL 경로 오류 | `picomotor.py`의 DLL 경로 확인 |
| `ValueError: The truth value of an array is ambiguous` | numpy 배열에 `or` 사용 | `arr if arr is not None else default` 로 변경 |
| GUI 멈춤 / 프리징 | 메인 스레드에서 무거운 작업 | QThread + Worker 패턴 사용 |
| 크래시 (segfault) | 워커 스레드에서 Qt 위젯 직접 접근 | `pyqtSignal`로 메인 스레드에 전달 |

---

## 개발 환경 권장 설정

| 도구 | 추천 | 이유 |
|------|------|------|
| **IDE** | PyCharm 또는 VS Code | PyQt6 자동완성 지원 |
| **Python** | 3.10 ~ 3.11 | pylablib 호환성 |
| **OS** | Windows 10/11 | HIKVISION/Picomotor SDK가 Windows 전용 |
| **빌드** | Nuitka | 실험실 PC에 Python 없이 배포할 때 |

```bash
# Nuitka로 exe 빌드
pip install nuitka
nuitka --standalone --enable-plugin=pyqt6 main.py
```

---

## 공부 우선순위 요약

```
🔴 필수 (이것 없으면 코드를 못 읽음)
  ├─ Python 기초 (클래스, 상속, 타입힌트)
  ├─ PyQt6 시그널-슬롯
  ├─ PyQt6 QThread + Worker 패턴
  └─ numpy 배열 기초

🟡 중요 (기능 추가/수정 시 필요)
  ├─ PyQt6 레이아웃 (VBox/HBox/Grid)
  ├─ PyQt6 QDockWidget
  ├─ opencv-python (이진화, centroid)
  └─ pyqtgraph (플롯, 히스토그램)

🟢 필요 시 (하드웨어 담당자만)
  ├─ HIKVISION MVS SDK
  ├─ pylablib (Picam)
  ├─ pythonnet/clr (Picomotor DLL)
  └─ SPE 바이너리 포맷
```
