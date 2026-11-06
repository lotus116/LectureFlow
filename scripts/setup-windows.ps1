# Run from native PowerShell on Windows x64, with CPython 3.12 and uv on PATH.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
if (!(Test-Path .venv/Scripts/python.exe)) {
    py -3.12 -m venv .venv
    if ($LASTEXITCODE) { throw "Python 3.12 x64 is required" }
}
uv pip install --python .venv/Scripts/python.exe -e ".[dev]" numpy==2.5.4 sherpa-onnx==2.0.1 sounddevice==0.5.6 psutil==7.2.2 pyinstaller==6.22.3
if ($LASTEXITCODE) { throw "Dependency installation failed" }

function Fetch($url, $path, $sha) {
    if ((Test-Path $path) -and ((Get-FileHash $path).Hash -eq $sha)) { return }
    New-Item -Type Directory (Split-Path $path) -Force | Out-Null
    curl.exe --fail --location --retry 2 --silent --show-error $url --output "$path.partial"
    if ($LASTEXITCODE -or ((Get-FileHash "$path.partial").Hash -ne $sha)) {
        throw "Download or checksum failed for $path"
    }
    Move-Item -LiteralPath "$path.partial" -Destination $path -Force
}
$base = "https://huggingface.co/csukuangfj2/sherpa-onnx-qwen3-asr-0.6B-int8-2026-03-25/resolve/68818b2313fe77bd06f6a7c5068ff3ef59d02b8a"
$weights = @{
    "conv_frontend.onnx" = "D22DC4423E0940E49884E903D2EA2F7E5567C14FC1AED97E4E26D6B8F208EF9E"
    "decoder.int8.onnx" = "4F6885BE5959AE26AF3089D38EE7972C5FAFBEEB1CF8D5E76EAB6D8B61CA5771"
    "encoder.int8.onnx" = "60748D3E6744A57C9C91E1B17424A6C2990567E8ADCEB0783940C03ED98FA9D9"
    "tokenizer/merges.txt" = "8831E4F1A044471340F7C0A83D7BD71306A5B867E95FD870F74D0C5308A904D5"
    "tokenizer/tokenizer_config.json" = "4942D005604266809309CABC9F4E9CB89CE855D59B14681FDC0E1CC62EA26C4C"
    "tokenizer/vocab.json" = "CA10D7E9FB3ED18575DD1E277A2579C16D108E32F27439684AFA0E10B1440910"
}
foreach ($name in $weights.Keys) { Fetch "$base/$name" "models/qwen/$name" $weights[$name] }
Copy-Item docs/licenses/Apache-2.0.txt models/qwen/LICENSE.txt -Force
$mt = "https://huggingface.co/tencent/Hy-MT2-1.8B-GGUF/resolve/a0c709d9fac510f2c807aa3af52872340dc37a4a"
Fetch "$mt/Hy-MT2-1.8B-Q4_K_M.gguf" models/Hy-MT2-Q4.gguf DC5F44FCF1FA496EE7AD725982C0C8C553A4DE00259B53AF84C4B89FB0C06699
Fetch "$mt/LICENSE.txt" models/Hy-MT2-LICENSE.txt A1D52D448F81C584A47C583E19DFAB2D3851C7C84431B07BAEE093C1113ED114
Fetch "https://github.com/ggml-org/llama.cpp/releases/download/b9000/llama-b9000-bin-win-vulkan-x64.zip" .tools/llama.zip 6900D078DC7B7DDCB7F56A5DB7EEE14E82B68854DABEC9F0CD12124F98C63812
Expand-Archive .tools/llama.zip .tools/llama -Force
Fetch "https://raw.githubusercontent.com/ggml-org/llama.cpp/b9000/LICENSE" .tools/llama/LICENSE 94F29BBED6A22C35B992C5C6EBF0E7C92F13B836B90F36F461C9CF2F0F1D010D
