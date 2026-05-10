import os
import re

file_path = r"d:\01_Project_Work\03. AMMI\SpeAnalyze\core\motor\acs_stage.py"
with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
    content = f.read()

# 1. Add signals to AcsStageController
if 'positions_updated = pyqtSignal(list)' not in content:
    content = content.replace(
        'class AcsStageController(QObject):',
        'class AcsStageController(QObject):\n    positions_updated = pyqtSignal(list)\n    states_updated    = pyqtSignal(list)\n    connection_lost   = pyqtSignal()'
    )

# 2. Overhaul start_polling
start_polling_pattern = r'def start_polling\(self, on_positions, on_states, on_lost\):.*?self\._thread\.start\(\)'
new_start_polling = """def start_polling(self, on_positions=None, on_states=None, on_lost=None):
        if not self._connected: return
        
        if on_positions: self.positions_updated.connect(on_positions)
        if on_states: self.states_updated.connect(on_states)
        if on_lost: self.connection_lost.connect(on_lost)

        if self._thread and self._thread.isRunning():
            return

        self._thread = QThread()
        self._worker = AcsWorker()
        self._worker.set_connection_params(*self._conn_info)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.setup)
        
        self._worker.positions_updated.connect(self._update_positions)
        self._worker.states_updated.connect(self._update_states)
        
        self._worker.positions_updated.connect(self.positions_updated.emit)
        self._worker.states_updated.connect(self.states_updated.emit)
        self._worker.connection_lost.connect(self.connection_lost.emit)
        self._thread.start()"""

content = re.sub(start_polling_pattern, new_start_polling, content, flags=re.DOTALL)

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)
