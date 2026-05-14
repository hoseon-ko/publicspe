import os

file_path = r"d:\01_Project_Work\03. AMMI\SpeAnalyze\ui\live\camera_panel.py"
with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
    lines = f.readlines()

fixed_lines = []
for i in range(len(lines)):
    line = lines[i]
    fixed_lines.append(line)
    
    # If this line ends with a colon (and is a for/if/def/etc.)
    stripped = line.strip()
    if stripped.endswith(":") and not stripped.startswith("#"):
        # Check next line
        if i + 1 < len(lines):
            next_line = lines[i+1]
            if next_line.strip() == "": continue
            
            # Calculate current indentation
            current_indent = len(line) - len(line.lstrip())
            next_indent = len(next_line) - len(next_line.lstrip())
            
            if next_indent <= current_indent:
                # Need to fix next line indentation
                lines[i+1] = (" " * (current_indent + 4)) + next_line.lstrip()

# Write back
with open(file_path, 'w', encoding='utf-8') as f:
    f.writelines(fixed_lines)
