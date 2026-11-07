param([switch]$Staging)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
$distPath = if ($Staging) { ".build/windows-candidate" } else { "dist/WindowsTrial" }
$target = Join-Path $distPath "LectureFlow"
$logo = (Resolve-Path docs/assets/logo.png).Path
& .venv/Scripts/python -m PyInstaller --noconfirm --clean --windowed --onedir --name LectureFlow --distpath "$distPath" --workpath .build/windows --specpath .build --icon "$logo" --add-data "${logo};docs/assets" --collect-all tkinterdnd2 --additional-hooks-dir scripts/pyinstaller-hooks --hidden-import pystray._win32 --collect-all pypdfium2_raw --collect-all winrt --collect-all sherpa_onnx --collect-all imageio_ffmpeg --collect-data sounddevice --collect-binaries _sounddevice_data scripts/windows_entry.py
if ($LASTEXITCODE) { throw "PyInstaller failed" }
New-Item -Type Directory "$target/models","$target/llama" -Force | Out-Null
Copy-Item models/qwen "$target/models" -Recurse
Copy-Item models/Hy-MT2-Q4.gguf,models/Hy-MT2-LICENSE.txt "$target/models"
Copy-Item .tools/llama/llama-server.exe,.tools/llama/LICENSE "$target/llama"
Get-ChildItem .tools/llama/*.dll -Exclude ggml-vulkan.dll | Copy-Item -Destination "$target/llama"
New-Item -Type Directory "$target/llama/vulkan" -Force | Out-Null
Copy-Item .tools/llama/ggml-vulkan.dll "$target/llama/vulkan"
Copy-Item docs/research/WINDOWS-TRIAL.md (Join-Path $target "试用说明.txt") -Force

# Preserve notices outside the frozen archive, including LGPL/GPL and Python text.
& .venv/Scripts/python scripts/collect_licenses.py --destination "$target/Licenses" --site .venv/Lib/site-packages
if ($LASTEXITCODE) { throw "License collection failed" }
Copy-Item LICENSE,NOTICE.md $target
& .venv/Scripts/python scripts/check_windows_licenses.py $target
if ($LASTEXITCODE) { throw "Frozen license/source checks failed" }
