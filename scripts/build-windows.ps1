$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
$target = "dist/WindowsTrial/LectureFlow"
& .venv/Scripts/python -m PyInstaller --noconfirm --clean --windowed --onedir --name LectureFlow --distpath dist/WindowsTrial --workpath .build/windows --specpath .build --collect-all sherpa_onnx --collect-all imageio_ffmpeg --collect-data sounddevice --collect-binaries _sounddevice_data scripts/windows_entry.py
if ($LASTEXITCODE) { throw "PyInstaller failed" }
New-Item -Type Directory "$target/models","$target/llama" -Force | Out-Null
Copy-Item models/qwen "$target/models" -Recurse
Copy-Item models/Hy-MT2-Q4.gguf,models/Hy-MT2-LICENSE.txt "$target/models"
Copy-Item .tools/llama/llama-server.exe,.tools/llama/*.dll,.tools/llama/LICENSE "$target/llama"
