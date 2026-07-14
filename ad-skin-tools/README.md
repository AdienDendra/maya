Requirements:
- Autodesk Maya 2023+
- Python 3.9+
- NumPy 1.23.5 recommended

If you are using Maya 2023 install NumPy in the Windows PowerShell:
& "C:\Program Files\Autodesk\Maya2023\bin\mayapy.exe" -m ensurepip --upgrade
& "C:\Program Files\Autodesk\Maya2023\bin\mayapy.exe" -m pip install --upgrade pip
& "C:\Program Files\Autodesk\Maya2023\bin\mayapy.exe" -m pip install "numpy==1.23.5"


If you are using Maya 2024, 2025 or 2026 install NumPy in the Windows PowerShell:
& "C:\Program Files\Autodesk\Maya2024\bin\mayapy.exe" -m ensurepip --upgrade
& "C:\Program Files\Autodesk\Maya2024\bin\mayapy.exe" -m pip install --upgrade pip
& "C:\Program Files\Autodesk\Maya2024\bin\mayapy.exe" -m pip install "numpy<2"

& "C:\Program Files\Autodesk\Maya2025\bin\mayapy.exe" -m ensurepip --upgrade
& "C:\Program Files\Autodesk\Maya2025\bin\mayapy.exe" -m pip install --upgrade pip
& "C:\Program Files\Autodesk\Maya2025\bin\mayapy.exe" -m pip install "numpy<2"

& "C:\Program Files\Autodesk\Maya2026\bin\mayapy.exe" -m ensurepip --upgrade
& "C:\Program Files\Autodesk\Maya2026\bin\mayapy.exe" -m pip install --upgrade pip
& "C:\Program Files\Autodesk\Maya2026\bin\mayapy.exe" -m pip install "numpy<2"