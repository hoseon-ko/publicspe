import sys
from PyQt6.QtCore import QSettings, QCoreApplication

# CLI 환경에서 QCoreApplication이 없으면 QSettings가 조직명/앱명을 인식하지 못할 수 있으므로 강제 초기화
app = QCoreApplication(sys.argv)

s = QSettings("SpeAnalyze", "MainWindow")
print("================ QSETTINGS KEYS ================")
for key in sorted(s.allKeys()):
    print(f"  {key}: {s.value(key)}")
print("================================================")
