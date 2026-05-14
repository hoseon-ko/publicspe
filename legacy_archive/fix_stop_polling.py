import os
import re

file_path = r"d:\01_Project_Work\03. AMMI\SpeAnalyze\core\motor\acs_stage.py"
with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
    content = f.read()

# Fix stop_polling to use invokeMethod safely
stop_polling_pattern = r'def stop_polling\(self\):.*?self\._thread\.wait\(500\)'
new_stop_polling = """def stop_polling(self):
        if self._worker:
            from PyQt6.QtCore import QMetaObject, Qt
            QMetaObject.invokeMethod(self._worker, "stop", Qt.ConnectionType.BlockingQueuedConnection)
        if self._thread:
            self._thread.quit()
            self._thread.wait()
        self._thread = None
        self._worker = None"""

# Try a simpler match if the complex one fails
if not re.search(stop_polling_pattern, content, flags=re.DOTALL):
    stop_polling_pattern = r'def stop_polling\(self\):.*?self\._worker = None'

content = re.sub(stop_polling_pattern, new_stop_polling, content, flags=re.DOTALL)

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)
