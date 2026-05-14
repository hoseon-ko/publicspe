import os

file_path = r"d:\01_Project_Work\03. AMMI\SpeAnalyze\ui\live\camera_panel.py"
with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
    lines = f.readlines()

new_lines = []
for i, line in enumerate(lines):
    # Check if this line is part of the ADC loop that failed
    if 'for key, label in [("adc_quality", "Quality")' in line:
        new_lines.append(line)
        # Indent subsequent lines until the end of loop
        # We need to find where the loop ends. 
        # In the previously viewed code, it was 4-5 lines.
        continue
    
    # Simple fix: if a line starts with 'r = QHBoxLayout()' and it's inside the ADC area
    if 'r = QHBoxLayout()' in line and i > 200 and i < 230:
        new_lines.append("                " + line.lstrip())
        continue
    if 'lbl = QLabel(f"{label}:")' in line and i > 200 and i < 230:
         new_lines.append("                " + line.lstrip())
         continue
    if 'cb = QComboBox()' in line and i > 200 and i < 230:
         new_lines.append("                " + line.lstrip())
         continue
    if 'r.addWidget(lbl)' in line and i > 200 and i < 230:
         new_lines.append("                " + line.lstrip())
         continue
    if 'r.addWidget(cb, 1)' in line and i > 200 and i < 230:
         new_lines.append("                " + line.lstrip())
         continue
    if 'lay_adc.addLayout(r)' in line and i > 200 and i < 230:
         new_lines.append("                " + line.lstrip())
         continue
    if 'self._adc_combos[key] = cb' in line and i > 200 and i < 230:
         new_lines.append("                " + line.lstrip())
         continue

    new_lines.append(line)

with open(file_path, 'w', encoding='utf-8') as f:
    f.writelines(new_lines)
