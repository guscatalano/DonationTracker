$ErrorActionPreference = "Stop"
if (-not (Test-Path .venv)) {
    python -m venv .venv
    .\.venv\Scripts\python.exe -m pip install -r requirements.txt
}

$port = if ($env:PORT) { $env:PORT } else { "8000" }
$args = @("-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", $port, "--reload")

if ($env:USE_HTTPS -eq "1" -or $env:USE_HTTPS -eq "true") {
    $keyFile = $env:SSL_KEY_FILE
    $certFile = $env:SSL_CERT_FILE
    if (-not $keyFile -or -not $certFile) {
        # Generate / reuse a self-signed cert under data/certs/
        $out = & .\.venv\Scripts\python.exe -m app.tls --ensure
        foreach ($line in $out) {
            if ($line -like "key=*")  { $keyFile  = $line.Substring(4) }
            if ($line -like "cert=*") { $certFile = $line.Substring(5) }
        }
        Write-Host "Using self-signed cert: $certFile"
        Write-Host "(Browsers will warn the first time. Accept to continue.)"
    }
    $args += @("--ssl-keyfile", $keyFile, "--ssl-certfile", $certFile)
    Write-Host "HTTPS on https://localhost:$port"
} else {
    Write-Host "HTTP on http://localhost:$port  (set USE_HTTPS=1 for TLS)"
}

& .\.venv\Scripts\python.exe @args
