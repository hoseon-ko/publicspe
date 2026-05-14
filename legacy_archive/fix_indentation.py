import os

file_path = r"d:\01_Project_Work\03. AMMI\SpeAnalyze\ui\live\camera_panel.py"
with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
    lines = f.readlines()

new_lines = []
in_build_ui = False
in_attach = False

for line in lines:
    if "def _build_ui(self):" in line:
        in_build_ui = True
        new_lines.append("    def _build_ui(self):\n")
        continue
    if in_build_ui:
        if line.strip() == "" or line.startswith("        ") or line.startswith("    "):
            # If it looks like it belongs to the class or is empty, keep going
            # but if we hit the next method, stop
            if "def " in line and not line.startswith("        ") and not line.startswith("    "):
                in_build_ui = False
            else:
                if line.strip() != "":
                    # Force 8 space indent for body
                    new_lines.append("        " + line.lstrip())
                else:
                    new_lines.append("\n")
                continue
        else:
            in_build_ui = False
    
    if "def attach_camera(self, camera: BaseCamera):" in line:
        in_attach = True
        new_lines.append("    def attach_camera(self, camera: BaseCamera):\n")
        continue
    if in_attach:
        if line.strip() == "" or line.startswith("        ") or line.startswith("    "):
            if "def " in line and not line.startswith("        ") and not line.startswith("    "):
                in_attach = False
            else:
                if line.strip() != "":
                    new_lines.append("        " + line.lstrip())
                else:
                    new_lines.append("\n")
                continue
        else:
            in_attach = False
            
    new_lines.append(line)

with open(file_path, 'w', encoding='utf-8') as f:
    f.writelines(new_lines)
