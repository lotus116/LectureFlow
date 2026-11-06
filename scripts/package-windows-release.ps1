# Package the tested standalone directory. GitHub limits each release asset to under 2 GiB.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
$version = (Get-Content VERSION -Raw).Trim()
$commit = (git rev-parse HEAD).Trim()
$folder = (Resolve-Path dist/WindowsTrial/LectureFlow).Path
$out = (New-Item -ItemType Directory -Path dist/windows-release -Force).FullName
if (Get-ChildItem $out) { throw "Release output must be empty" }
foreach ($item in @("LectureFlow.exe", "models/qwen/decoder.int8.onnx", "models/Hy-MT2-Q4.gguf", "llama/llama-server.exe")) {
    if (!(Test-Path -LiteralPath (Join-Path $folder $item) -PathType Leaf)) { throw "Missing $item" }
}
@{version=$version;commit=$commit;platform="Windows-x64"} | ConvertTo-Json | Set-Content "$folder/build-info.json" -Encoding utf8
Copy-Item docs/licenses/README.md "$folder/模型来源.md"
Copy-Item "docs/RELEASE-$version.md" "$folder/版本说明.md"
# Start the frozen executable with a disposable library and verify backend initialization.
$probe = Join-Path ([System.IO.Path]::GetTempPath()) ("lf-release-" + [guid]::NewGuid())
$process = Start-Process -FilePath "$folder/LectureFlow.exe" -ArgumentList "--worker --cpu --library `"$probe`" --stop-token `"$probe/stop`"" -PassThru
if (!$process.WaitForExit(90000)) { $process.Kill(); throw "Packaged worker timed out" }
if ($process.ExitCode -ne 0 -or !(Test-Path "$probe/Library.sqlite3")) { throw "Packaged worker did not initialize a library" }
Remove-Item $probe -Recurse -Force
Push-Location (Split-Path $folder)
try {
    $name = "LectureFlow-$version-Windows-x64"
    $zip = Join-Path $out "$name.zip"
    & 7z a -tzip -mx=3 $zip LectureFlow
    if ($LASTEXITCODE) { throw "Archive failed" }
    if ((Get-Item $zip).Length -ge 2147483648) {
        Remove-Item $zip
        & 7z a -tzip -mx=3 $zip LectureFlow "-x!LectureFlow/models/Hy-MT2-Q4.gguf"
        if ($LASTEXITCODE) { throw "App archive failed" }
        & 7z a -tzip -mx=3 (Join-Path $out "$name-translation.zip") "LectureFlow/models/Hy-MT2-Q4.gguf"
        if ($LASTEXITCODE) { throw "Translation archive failed" }
    }
    foreach ($file in (Get-ChildItem $out -Filter *.zip)) {
        if ($file.Length -ge 2147483648) { throw "Archive exceeds release limit" }
        & 7z t $file.FullName
        if ($LASTEXITCODE) { throw "Archive check failed" }
    }
} finally { Pop-Location }
