"""DeepAlign 스캔 워크플로우 위젯 (3 하드웨어).

장치 패널과 분리된 스캔 전용 UI. 각 위젯은:
  - 자체 입력 (포인트 설정 / settle / avg)
  - scan_requested(points, settle_ms, avg_frames) 시그널 emit
  - set_scan_status / set_scan_running 으로 외부(main_tab)가 상태 갱신
워커/무버 자체는 ui/deepalign/scan/ 패키지의 기존 클래스를 그대로 활용.
"""

from ui.deepalign.scan.scan_widgets.mirror_scan_widget import MirrorScanWidget
from ui.deepalign.scan.scan_widgets.kimm_scan_widget   import KimmScanWidget
from ui.deepalign.scan.scan_widgets.acs_scan_widget    import AcsScanWidget

__all__ = ["MirrorScanWidget", "KimmScanWidget", "AcsScanWidget"]
