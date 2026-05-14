import os
import glob
import re

directories = [
    r"d:\01_Project_Work\03. AMMI\SpeAnalyze\ui\live",
    r"d:\01_Project_Work\03. AMMI\SpeAnalyze\ui\autofocus",
    r"d:\01_Project_Work\03. AMMI\SpeAnalyze\ui\acquisition",
    r"d:\01_Project_Work\03. AMMI\SpeAnalyze\ui\scan",
    r"d:\01_Project_Work\03. AMMI\SpeAnalyze\ui\analysis",
    r"d:\01_Project_Work\03. AMMI\SpeAnalyze\ui\kinematic",
    r"d:\01_Project_Work\03. AMMI\SpeAnalyze\ui\viewer",
    r"d:\01_Project_Work\03. AMMI\SpeAnalyze\theme"
]

def replacer(match):
    space = match.group(1)
    size = int(match.group(2))
    bump = 3 if size < 20 else 4
    return f"font-size:{space}{size + bump}px"

def var_replacer(match):
    var_name = match.group(1)
    size = int(match.group(2))
    bump = 3 if size < 20 else 4
    return f'{var_name} = "{size + bump}px"'

count = 0
for d in directories:
    if not os.path.exists(d): continue
    for fpath in glob.glob(os.path.join(d, "*.py")):
        with open(fpath, "r", encoding="utf-8") as f:
            content = f.read()
            
        orig = content
        # CSS font-size
        content = re.sub(r"font-size:(\s*)(\d+)px", replacer, content)
        # Variable string assignments like TITLE  = "18px"
        content = re.sub(r"([A-Z_a-z]+)\s*=\s*\"(\d+)px\"", var_replacer, content)
        
        if content != orig:
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(content)
            count += 1
            print(f"Updated {os.path.basename(fpath)}")

print(f"Done. Updated {count} files.")
