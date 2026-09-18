# =============================================================
#  toio keyboard driver - local launcher
#
#  Starts a tiny local web server and opens the control page in
#  the default browser. Web Bluetooth does not work from file://
#  URLs, so the page must be served over http://localhost.
#
#  Usage:  double-click toio-keyboard.bat
#          or: powershell -ExecutionPolicy Bypass -File start-toio.ps1
#  Stop :  press Ctrl + C in this window
#
#  NOTE: keep this file ASCII-only. Windows PowerShell 5.1 reads
#  .ps1 files using the system ANSI code page, so non-ASCII text
#  saved as UTF-8 without BOM breaks the parser.
# =============================================================

param([switch]$NoBrowser)

$ErrorActionPreference = 'Stop'

$root = $PSScriptRoot
if (-not $root) { $root = Split-Path -Parent $MyInvocation.MyCommand.Path }

$page = 'toio-keyboard.html'

if (-not (Test-Path -LiteralPath (Join-Path $root $page))) {
    Write-Host "ERROR: $page not found next to this script." -ForegroundColor Red
    Write-Host "Folder: $root"
    Read-Host "Press Enter to close"
    exit 1
}

# --- find a free port and start listening -------------------
$listener = $null
$port = 0
for ($p = 8000; $p -le 8050; $p++) {
    $candidate = New-Object System.Net.HttpListener
    $candidate.Prefixes.Add("http://localhost:$p/")
    try {
        $candidate.Start()
        $listener = $candidate
        $port = $p
        break
    } catch {
        try { $candidate.Close() } catch {}
    }
}

if (-not $listener) {
    Write-Host "ERROR: could not start a local server on ports 8000-8050." -ForegroundColor Red
    Write-Host "If Python is installed, this works too:  python -m http.server 8000" -ForegroundColor Yellow
    Read-Host "Press Enter to close"
    exit 1
}

$url = "http://localhost:$port/$page"

Write-Host ""
Write-Host "  toio keyboard driver - server running" -ForegroundColor Cyan
Write-Host "  $url" -ForegroundColor White
Write-Host ""
Write-Host "  Open that address in Chrome or Edge." -ForegroundColor Gray
Write-Host "  Keep this window open while you drive the cube." -ForegroundColor Gray
Write-Host "  Press Ctrl + C to stop." -ForegroundColor DarkGray
Write-Host ""

if (-not $NoBrowser) { Start-Process $url }

$mime = @{
    '.html' = 'text/html; charset=utf-8'
    '.htm'  = 'text/html; charset=utf-8'
    '.js'   = 'text/javascript; charset=utf-8'
    '.css'  = 'text/css; charset=utf-8'
    '.json' = 'application/json; charset=utf-8'
    '.md'   = 'text/plain; charset=utf-8'
    '.png'  = 'image/png'
    '.jpg'  = 'image/jpeg'
    '.svg'  = 'image/svg+xml'
    '.ico'  = 'image/x-icon'
}

$rootFull = [System.IO.Path]::GetFullPath($root)

try {
    while ($listener.IsListening) {
        $ctx = $listener.GetContext()
        $res = $ctx.Response

        $rel = [System.Uri]::UnescapeDataString($ctx.Request.Url.AbsolutePath.TrimStart('/'))
        if ([string]::IsNullOrWhiteSpace($rel)) { $rel = $page }

        $target = Join-Path $root $rel
        $full = $null
        try { $full = [System.IO.Path]::GetFullPath($target) } catch {}

        if ((-not $full) -or (-not $full.StartsWith($rootFull))) {
            $res.StatusCode = 403
            $res.Close()
            continue
        }

        if (Test-Path -LiteralPath $full -PathType Leaf) {
            $bytes = [System.IO.File]::ReadAllBytes($full)
            $ext = [System.IO.Path]::GetExtension($full).ToLower()
            if ($mime.ContainsKey($ext)) { $res.ContentType = $mime[$ext] }
            else { $res.ContentType = 'application/octet-stream' }
            $res.ContentLength64 = $bytes.Length
            $res.OutputStream.Write($bytes, 0, $bytes.Length)
            Write-Host "  200  $rel" -ForegroundColor DarkGray
        } else {
            $res.StatusCode = 404
            $msg = [System.Text.Encoding]::UTF8.GetBytes("404 Not Found: $rel")
            $res.OutputStream.Write($msg, 0, $msg.Length)
            Write-Host "  404  $rel" -ForegroundColor DarkYellow
        }
        $res.Close()
    }
}
finally {
    try { $listener.Stop() } catch {}
    try { $listener.Close() } catch {}
    Write-Host "Server stopped." -ForegroundColor Cyan
}
