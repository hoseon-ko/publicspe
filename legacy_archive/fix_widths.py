import re

def adjust_widths(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Increase setFixedWidth(X) by roughly 25-30%
    def replace_width(match):
        width = int(match.group(1))
        # Add 12 to 20 pixels depending on size to ensure fit
        new_width = width + max(12, int(width * 0.25))
        return f"setFixedWidth({new_width})"

    content = re.sub(r"setFixedWidth\((\d+)\)", replace_width, content)
    
    # Also fix the header tuple array in acs_stage_panel:
    # ("Axis", 28), ("Position", 100), ("", 14), ("", 32), ("", 34), ("Step(mm)", 72), ("", 26), ("", 26)
    # We can just bump any raw integer in that specific line?
    # No, it's safer to just replace them manually or regex specifically.
    content = content.replace('("Axis", 28)', '("Axis", 40)')
    content = content.replace('("Position", 100)', '("Position", 130)')
    content = content.replace('("", 14)', '("", 20)')
    content = content.replace('("", 32)', '("", 42)')
    content = content.replace('("", 34)', '("", 44)')
    content = content.replace('("Step(mm)", 72)', '("Step(mm)", 90)')
    content = content.replace('("", 26)', '("", 36)')
    
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)

adjust_widths(r"d:\01_Project_Work\03. AMMI\SpeAnalyze\ui\live\acs_stage_panel.py")
adjust_widths(r"d:\01_Project_Work\03. AMMI\SpeAnalyze\ui\live\kimm_z_panel.py")
print("Fixed widths!")
