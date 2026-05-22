import os

output_file = r"d:\01_Project_Work\03. AMMI\SpeAnalyze\scratch\search_results.txt"

with open(output_file, 'w', encoding='utf-8') as out:
    out.write("Pure text search started\n")
    try:
        path = r"C:\Program Files (x86)\MVS\Development\Samples\Python\MvImport\MvCameraControl_class.py"
        if os.path.exists(path):
            out.write("MvCameraControl_class.py exists\n")
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                for i, line in enumerate(f, 1):
                    if 'CallBack' in line or 'callback' in line.lower():
                        out.write(f"{i}: {line.strip()}\n")
        else:
            out.write("MvCameraControl_class.py NOT found\n")
            
    except Exception as e:
        import traceback
        out.write(f"Error: {e}\n")
        traceback.print_exc(file=out)

out.write("Pure text search finished\n")
