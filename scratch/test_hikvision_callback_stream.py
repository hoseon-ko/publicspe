import sys
import time
import numpy as np
from PyQt6.QtCore import QCoreApplication, QTimer

# core 모듈 경로를 sys.path에 추가
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.camera.hikvision import HikvisionCamera

def main():
    print("=== 하이크비전 콜백 라이브 스트리밍 테스트 ===")
    
    # PyQt6 이벤트 루프 초기화 (디스패처 시그널 수신을 위해 필요)
    app = QCoreApplication(sys.argv)
    
    cam = HikvisionCamera(device_index=0)
    
    try:
        print("카메라 연결 중...")
        cam.connect()
        print(f"연결 성공! 모델: {cam.camera_model()}, 시리얼: {cam.camera_serial()}")
        
        frame_count = 0
        last_print = time.time()
        
        def on_frame(frame):
            nonlocal frame_count, last_print
            frame_count += 1
            now = time.time()
            if now - last_print >= 1.0:
                print(f"[수신] {frame_count} 프레임 획득 | 크기: {frame.shape} | 평균 FPS: {frame_count / (now - last_print):.1f}")
                frame_count = 0
                last_print = now

        print("\n콜백 스트리밍 시작...")
        cam.start_live(on_frame)
        print("스트리밍 중... (5초 동안 유지)")
        
        # 5초 뒤에 스트리밍 중지하고 앱 종료하는 타이머 설정
        def stop_test():
            print("\n스트리밍 중지 중...")
            cam.stop_live()
            print("카메라 연결 해제 중...")
            cam.disconnect()
            print("테스트 완료!")
            app.quit()
            
        QTimer.singleShot(5000, stop_test)
        
        # 이벤트 루프 실행
        sys.exit(app.exec())
        
    except Exception as e:
        print(f"테스트 중 오류 발생: {e}")
        if cam.is_connected:
            cam.disconnect()
        sys.exit(1)

if __name__ == "__main__":
    main()
