import re

file_path = r"d:\01_Project_Work\03. AMMI\SpeAnalyze\ui\live\acs_stage_panel.py"
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Remove the QHBoxLayout hdr logic
old_hdr = '''        # 헤더
        hdr = QHBoxLayout()
        for txt, width in [("Axis", 40), ("Position", 130), ("", 20),
                           ("", 42), ("", 44), ("Step(mm)", 90), ("", 36), ("", 36)]:
            lbl = QLabel(txt)
            lbl.setStyleSheet(f"color:{C_DIM}; font-family:'{_FC}'; font-size:12px;")
            lbl.setFixedWidth(width)
            hdr.addWidget(lbl)
        lay.addLayout(hdr)

        # 구분선
        line = QWidget()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background:{C_BORDER};")
        lay.addWidget(line)

        # 6축 그리드
        grid = QGridLayout()
        grid.setSpacing(4)
        grid.setContentsMargins(0, 4, 0, 0)
        for i in range(6):
            row = _AxisRow(i, grid, self._ctrl_ref, self._move_btns, self._log)
            self._axis_rows.append(row)
        lay.addLayout(grid)'''

new_hdr = '''        # 6축 그리드
        grid = QGridLayout()
        grid.setSpacing(4)
        grid.setContentsMargins(0, 4, 0, 0)
        
        # 헤더를 Grid의 row=0에 추가하여 칼럼 완벽 정렬
        headers = ["Axis", "Position", "", "", "", "Step(mm)", "", ""]
        for col, txt in enumerate(headers):
            lbl = QLabel(txt)
            lbl.setStyleSheet(f"color:{C_DIM}; font-family:'{_FC}'; font-size:13px; padding-bottom:4px;")
            if col == 1:
                lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            grid.addWidget(lbl, 0, col)

        for i in range(6):
            # _AxisRow 내부에서 row = idx + 1 로 수정 필요
            row_idx = i + 1
            row = _AxisRow(i, grid, self._ctrl_ref, self._move_btns, self._log)
            # wait, _AxisRow uses self.idx as the row inside grid. I need to modify _AxisRow as well.
            self._axis_rows.append(row)
        lay.addLayout(grid)'''

content = content.replace(old_hdr, new_hdr)

# Modify _AxisRow init to offset row by 1
content = content.replace("row = idx", "row = idx + 1")

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)
print("Aligned ACS headers to grid!")
