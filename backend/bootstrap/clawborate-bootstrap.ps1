param(
    [Parameter(Mandatory = $true)]
    [string]$SetupToken,
    [Parameter(Mandatory = $true)]
    [string]$ApiBase,
    [string]$OpenClawCli,
    [string]$OpenClawRoot,
    [string]$SkillHome
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$script:Utf8NoBom = [System.Text.UTF8Encoding]::new($false)
try {
    [Console]::InputEncoding = $script:Utf8NoBom
} catch {
}
try {
    [Console]::OutputEncoding = $script:Utf8NoBom
} catch {
}
$OutputEncoding = $script:Utf8NoBom

$script:OpenClawInvocation = @()
$script:LogPath = $null
$script:OpenClawRootPath = $null

function Split-CommandPrefix {
    param([string[]]$Prefix)
    if (-not $Prefix -or $Prefix.Count -eq 0) {
        throw "Command prefix is empty."
    }
    if ($Prefix.Count -eq 1) {
        return @{
            Exe = $Prefix[0]
            Args = @()
        }
    }
    return @{
        Exe = $Prefix[0]
        Args = @($Prefix[1..($Prefix.Count - 1)])
    }
}

function Write-Log {
    param([string]$Message)
    $timestamp = (Get-Date).ToString("s")
    $line = "[$timestamp] $Message"
    Write-Host $line
    if ($script:LogPath) {
        Add-Content -Path $script:LogPath -Value $line
    }
}

function Write-JsonFile {
    param(
        [string]$Path,
        [object]$Value
    )
    $parent = Split-Path -Parent $Path
    if ($parent) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    $json = ConvertTo-Json -InputObject $Value -Depth 100
    [System.IO.File]::WriteAllText($Path, $json, (New-Object System.Text.UTF8Encoding($false)))
}

function Read-JsonFile {
    param([string]$Path)
    if (-not (Test-Path $Path)) {
        return $null
    }
    return (Get-Content -Raw -Path $Path | ConvertFrom-Json)
}

function Invoke-OpenClaw {
    param(
        [string[]]$Arguments,
        [switch]$AllowFailure
    )
    $invocation = @($script:OpenClawInvocation)
    if (-not $invocation -or $invocation.Count -eq 0) {
        throw "OpenClaw CLI is not initialized."
    }
    $prefix = Split-CommandPrefix -Prefix $invocation
    $previousErrorActionPreference = $ErrorActionPreference
    $nativePrefWasPresent = $null -ne (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue)
    if ($nativePrefWasPresent) {
        $previousNativePreference = $PSNativeCommandUseErrorActionPreference
    }
    try {
        $ErrorActionPreference = "Continue"
        if ($nativePrefWasPresent) {
            $PSNativeCommandUseErrorActionPreference = $false
        }
        $output = & $prefix.Exe @($prefix.Args + $Arguments) 2>&1
        $exitCode = if ($null -ne $LASTEXITCODE) { $LASTEXITCODE } else { 0 }
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
        if ($nativePrefWasPresent) {
            $PSNativeCommandUseErrorActionPreference = $previousNativePreference
        }
    }
    $text = (
        $output |
        ForEach-Object {
            if ($_ -is [System.Management.Automation.ErrorRecord]) {
                $_.ToString()
            } else {
                "$_"
            }
        } |
        Out-String
    ).Trim()
    if (-not $AllowFailure -and $exitCode -ne 0) {
        throw "OpenClaw command failed ($exitCode): $text"
    }
    return [pscustomobject]@{
        ExitCode = $exitCode
        Text = $text
    }
}

function Convert-MixedTextToJson {
    param([string]$Text)
    if (-not $Text) {
        return $null
    }
    $trimmed = $Text.Trim()
    if (-not $trimmed) {
        return $null
    }
    try {
        return $trimmed | ConvertFrom-Json
    } catch {
    }

    $lines = @($trimmed -split "\r?\n")
    for ($start = 0; $start -lt $lines.Count; $start++) {
        $first = $lines[$start].TrimStart()
        if (-not ($first.StartsWith("{") -or $first.StartsWith("["))) {
            continue
        }
        for ($end = $lines.Count - 1; $end -ge $start; $end--) {
            $candidate = ($lines[$start..$end] -join "`n").Trim()
            if (-not $candidate) {
                continue
            }
            try {
                return $candidate | ConvertFrom-Json
            } catch {
            }
        }
    }
    return $null
}

function Format-OpenClawTextForLog {
    param([string]$Text)
    if (-not $Text) {
        return ""
    }
    $json = Convert-MixedTextToJson -Text $Text
    if ($json) {
        return (ConvertTo-Json -InputObject $json -Depth 50 -Compress)
    }

    $readable = New-Object System.Collections.Generic.List[string]
    foreach ($line in @($Text -split "\r?\n")) {
        $trimmed = $line.Trim()
        if (-not $trimmed) {
            continue
        }
        $asciiPrintable = 0
        foreach ($char in $trimmed.ToCharArray()) {
            $code = [int][char]$char
            if (($code -ge 32 -and $code -le 126) -or $code -eq 9) {
                $asciiPrintable++
            }
        }
        if ($trimmed.Length -gt 0 -and (($asciiPrintable / $trimmed.Length) -ge 0.75)) {
            if (-not $readable.Contains($trimmed)) {
                [void]$readable.Add($trimmed)
            }
        }
    }
    return ($readable -join "`n").Trim()
}

function Resolve-LocalCronStorePath {
    $candidateRoots = New-Object System.Collections.Generic.List[string]
    foreach ($root in @($script:OpenClawRootPath, $OpenClawRoot, (Join-Path ([Environment]::GetFolderPath("UserProfile")) ".openclaw"))) {
        if (-not $root) {
            continue
        }
        if (-not $candidateRoots.Contains($root)) {
            [void]$candidateRoots.Add($root)
        }
    }
    foreach ($root in $candidateRoots) {
        $path = Join-Path $root "cron\jobs.json"
        if (Test-Path $path) {
            return $path
        }
    }
    if ($candidateRoots.Count -gt 0) {
        return (Join-Path $candidateRoots[0] "cron\jobs.json")
    }
    return $null
}

function Resolve-CronJobIdFromLocalStore {
    param([string]$Name)
    $storePath = Resolve-LocalCronStorePath
    if (-not $storePath -or -not (Test-Path $storePath)) {
        return $null
    }
    try {
        $payload = Read-JsonFile -Path $storePath
    } catch {
        return $null
    }
    $jobs = @()
    if ($payload -and $payload.PSObject.Properties.Name -contains "jobs") {
        $jobs = @($payload.jobs)
    } elseif ($payload -is [System.Collections.IEnumerable] -and -not ($payload -is [string])) {
        $jobs = @($payload)
    } elseif ($payload -and $payload.id) {
        $jobs = @($payload)
    }
    foreach ($job in $jobs) {
        if ($job.name -eq $Name -and $job.id) {
            return [string]$job.id
        }
    }
    return $null
}

function Resolve-OpenClawInvocation {
    if ($OpenClawCli) {
        return @($OpenClawCli)
    }

    $candidate = Get-Command openclaw.cmd -ErrorAction SilentlyContinue
    if ($candidate) {
        return @($candidate.Source)
    }
    $candidate = Get-Command openclaw -ErrorAction SilentlyContinue
    if ($candidate) {
        return @($candidate.Source)
    }

    $npmCmd = Join-Path $env:APPDATA "npm\openclaw.cmd"
    if (Test-Path $npmCmd) {
        return @($npmCmd)
    }

    $gatewayCmd = Join-Path ([Environment]::GetFolderPath("UserProfile")) ".openclaw\gateway.cmd"
    if (Test-Path $gatewayCmd) {
        $content = Get-Content -Path $gatewayCmd
        foreach ($line in $content) {
            if ($line -match '^(?<node>[A-Za-z]:\\.+?node\.exe)\s+(?<script>[A-Za-z]:\\.+?openclaw\\(?:dist\\index\.js|openclaw\.mjs))\s+gateway\b') {
                if ((Test-Path $Matches["node"]) -and (Test-Path $Matches["script"])) {
                    return @($Matches["node"], $Matches["script"])
                }
            }
        }
    }

    throw "OpenClaw CLI was not detected. Install OpenClaw first or pass -OpenClawCli explicitly."
}

function Resolve-OpenClawConfigPath {
    $result = Invoke-OpenClaw -Arguments @("config", "file")
    $lines = @(
        $result.Text -split "\r?\n" |
        ForEach-Object { $_.Trim() } |
        Where-Object { $_ }
    )
    $path = $null
    [array]::Reverse($lines)
    foreach ($line in $lines) {
        if ($line -match '(?<path>(?:[A-Za-z]:\\|~[\\/]).*openclaw\.json)$') {
            $path = $Matches["path"]
            break
        }
    }
    if (-not $path -and $lines.Count -eq 1) {
        $path = $lines[0]
    }
    if (-not $path) {
        throw "Could not resolve active OpenClaw config path."
    }
    try {
        return $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($path)
    } catch {
        return $path
    }
}

function Resolve-OpenClawRootFromConfig {
    param([string]$ConfigPath)
    if ($OpenClawRoot) {
        return $OpenClawRoot
    }
    return Split-Path -Parent $ConfigPath
}

function Resolve-WorkspacePath {
    param([object]$Config, [string]$Root)
    if ($Config -and $Config.agents -and $Config.agents.defaults -and $Config.agents.defaults.workspace) {
        return [string]$Config.agents.defaults.workspace
    }
    return (Join-Path $Root "workspace")
}

function Resolve-SkillHome {
    param([string]$Root)
    if ($SkillHome) {
        return $SkillHome
    }
    return (Join-Path $Root "clawborate")
}

function Download-File {
    param(
        [string]$Url,
        [string]$Destination
    )
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Destination) | Out-Null
    Invoke-WebRequest -Uri $Url -OutFile $Destination
}

function Get-FileSha256 {
    param([string]$Path)
    return (Get-FileHash -Path $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Sync-Directory {
    param(
        [string]$Source,
        [string]$Destination
    )
    if (Test-Path $Destination) {
        Remove-Item -Recurse -Force $Destination
    }
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    Copy-Item -Path (Join-Path $Source "*") -Destination $Destination -Recurse -Force
}

function Resolve-PythonCommand {
    $candidates = @(
        @{ Command = "py"; Args = @("-3.11", "-c", "print('ok')") },
        @{ Command = "python3"; Args = @("-c", "print('ok')") },
        @{ Command = "python"; Args = @("-c", "print('ok')") }
    )
    foreach ($candidate in $candidates) {
        try {
            $null = & $candidate.Command @($candidate.Args) 2>$null
            if ($LASTEXITCODE -eq 0) {
                if ($candidate.Command -eq "py") {
                    return @("py", "-3.11")
                }
                return @($candidate.Command)
            }
        } catch {
        }
    }
    return $null
}

function Ensure-EmbeddedPythonSite {
    param([string]$PythonHome)
    $pth = Get-ChildItem -Path $PythonHome -Filter "python*._pth" -File -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $pth) {
        return
    }
    $lines = Get-Content -Path $pth.FullName
    $updated = @()
    $hasImportSite = $false
    foreach ($line in $lines) {
        if ($line -eq "#import site") {
            $updated += "import site"
            $hasImportSite = $true
        } else {
            if ($line -eq "import site") {
                $hasImportSite = $true
            }
            $updated += $line
        }
    }
    if (-not $hasImportSite) {
        $updated += "import site"
    }
    Set-Content -Path $pth.FullName -Value $updated -Encoding utf8
}

function Install-PrivatePython {
    param(
        [hashtable]$PythonRuntime,
        [string]$InstallRoot
    )
    $pythonRoot = Join-Path $InstallRoot "python"
    $downloadsDir = Join-Path $InstallRoot "downloads"
    $archivePath = Join-Path $downloadsDir "python-runtime.zip"
    $runtimeUrl = $PythonRuntime.url
    $runtimeSha = $PythonRuntime.sha256
    if (-not $runtimeUrl) {
        $runtimeUrl = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip"
        Write-Log "Python runtime URL not provided by server. Falling back to official embeddable CPython."
    }
    Download-File -Url $runtimeUrl -Destination $archivePath
    if ($runtimeSha) {
        $actual = Get-FileSha256 -Path $archivePath
        if ($actual -ne $runtimeSha.ToLowerInvariant()) {
            throw "Downloaded Python runtime sha256 mismatch."
        }
    }
    if (Test-Path $pythonRoot) {
        Remove-Item -Recurse -Force $pythonRoot
    }
    Expand-Archive -LiteralPath $archivePath -DestinationPath $pythonRoot -Force
    Ensure-EmbeddedPythonSite -PythonHome $pythonRoot
    $pythonExe = Join-Path $pythonRoot "python.exe"
    if (-not (Test-Path $pythonExe)) {
        throw "Private Python runtime did not contain python.exe."
    }
    $getPip = Join-Path $downloadsDir "get-pip.py"
    Download-File -Url "https://bootstrap.pypa.io/get-pip.py" -Destination $getPip
    & $pythonExe $getPip
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install pip into private Python runtime."
    }
    & $pythonExe -m pip install "requests>=2.31.0,<3.0.0"
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install requests into private Python runtime."
    }
    return @($pythonExe)
}

function Backup-LegacyState {
    param([string]$SkillHomePath)
    $statePath = Join-Path $SkillHomePath "state.json"
    if (-not (Test-Path $statePath)) {
        return $null
    }
    $backupDir = Join-Path $SkillHomePath "migration"
    New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
    $backupPath = Join-Path $backupDir "legacy-plugin-state.json"
    Copy-Item -Path $statePath -Destination $backupPath -Force
    return $backupPath
}

function Merge-LegacyState {
    param(
        [string]$SkillHomePath,
        [string]$BackupPath,
        [object]$LegacyPluginConfig
    )
    if (-not $BackupPath -or -not (Test-Path $BackupPath)) {
        return
    }
    $legacy = Read-JsonFile -Path $BackupPath
    $statePath = Join-Path $SkillHomePath "state.json"
    $current = Read-JsonFile -Path $statePath
    if (-not $current) {
        $current = @{}
    }
    if (-not $current.bootstrap) {
        $current | Add-Member -NotePropertyName bootstrap -NotePropertyValue ([pscustomobject]@{}) -Force
    }
    if ($current.bootstrap -is [System.Collections.IDictionary]) {
        $current.bootstrap["legacy_plugin_state"] = $legacy
        if ($LegacyPluginConfig) {
            $current.bootstrap["legacy_plugin_config"] = $LegacyPluginConfig
        }
    } else {
        Add-Member -InputObject $current.bootstrap -NotePropertyName legacy_plugin_state -NotePropertyValue $legacy -Force
        if ($LegacyPluginConfig) {
            Add-Member -InputObject $current.bootstrap -NotePropertyName legacy_plugin_config -NotePropertyValue $LegacyPluginConfig -Force
        }
    }
    Write-JsonFile -Path $statePath -Value $current
}

function Invoke-ConfigBatchOrFallback {
    param(
        [string]$BatchFile,
        [object[]]$Operations
    )
    $dryRun = Invoke-OpenClaw -Arguments @("config", "set", "--batch-file", $BatchFile, "--dry-run") -AllowFailure
    if ($dryRun.ExitCode -eq 0) {
        $null = Invoke-OpenClaw -Arguments @("config", "set", "--batch-file", $BatchFile)
        return
    }
    Write-Log "Batch config apply failed; falling back to individual config set operations."
    foreach ($op in $Operations) {
        $jsonValue = ($op.value | ConvertTo-Json -Compress -Depth 20)
        $null = Invoke-OpenClaw -Arguments @("config", "set", $op.path, $jsonValue, "--strict-json")
    }
}

function Resolve-DeliveryTarget {
    param([object]$Config)
    $channel = $null
    $to = $null
    $account = $null

    if ($Config -and $Config.plugins -and $Config.plugins.entries -and $Config.plugins.entries.clawborate -and $Config.plugins.entries.clawborate.config) {
        $overrides = $Config.plugins.entries.clawborate.config.channelOverrides
        if ($overrides -and $overrides.Count -gt 0) {
            $first = [string]$overrides[0]
            $parts = $first.Split(":", 2)
            if ($parts.Count -eq 2) {
                $channel = $parts[0]
                $to = $parts[1]
            }
        }
    }

    if ($Config -and $Config.bindings -and $Config.bindings.Count -gt 0) {
        foreach ($binding in $Config.bindings) {
            if ($binding.match) {
                if (-not $channel -and $binding.match.channel) {
                    $channel = [string]$binding.match.channel
                }
                if (-not $account -and $binding.match.accountId) {
                    $account = [string]$binding.match.accountId
                }
                if ($channel -and $account) {
                    break
                }
            }
        }
    }

    return @{
        channel = $channel
        to = $to
        account = $account
    }
}

function Resolve-CronJobIdByName {
    param([string]$Name)
    $list = Invoke-OpenClaw -Arguments @("cron", "list", "--json") -AllowFailure
    if ($list.ExitCode -eq 0) {
        $payload = Convert-MixedTextToJson -Text $list.Text
        if ($payload) {
            $jobs = @()
            if ($payload.PSObject.Properties.Name -contains "jobs") {
                $jobs = @($payload.jobs)
            } elseif ($payload -is [System.Collections.IEnumerable] -and -not ($payload -is [string])) {
                $jobs = @($payload)
            } elseif ($payload.id) {
                $jobs = @($payload)
            }
            foreach ($job in $jobs) {
                if ($job.name -eq $Name -and $job.id) {
                    return [string]$job.id
                }
            }
        }
    }
    return (Resolve-CronJobIdFromLocalStore -Name $Name)
}

function Resolve-CronJobIdFromAddOutput {
    param(
        [string]$Text,
        [string]$ExpectedName
    )
    $row = Convert-MixedTextToJson -Text $Text
    if (-not $row) {
        return $null
    }
    if ($row.id -and (-not $ExpectedName -or $row.name -eq $ExpectedName)) {
        return [string]$row.id
    }
    if ($row.PSObject.Properties.Name -contains "jobs") {
        foreach ($job in @($row.jobs)) {
            if ($job.id -and (-not $ExpectedName -or $job.name -eq $ExpectedName)) {
                return [string]$job.id
            }
        }
    }
    return (Resolve-CronJobIdFromLocalStore -Name $ExpectedName)
}

function Ensure-CronJob {
    param(
        [hashtable]$CronSpec,
        [hashtable]$Delivery
    )
    $baseArgs = @(
        "--name", $CronSpec.name,
        "--description", "Clawborate per-project patrol",
        "--agent", $CronSpec.agent,
        "--session", $CronSpec.session,
        "--session-key", $CronSpec.session_key,
        "--every", $CronSpec.every,
        "--message", $CronSpec.message,
        "--light-context",
        "--best-effort-deliver",
        "--announce"
    )
    if ($Delivery.channel) {
        $baseArgs += @("--channel", $Delivery.channel)
    }
    if ($Delivery.account) {
        $baseArgs += @("--account", $Delivery.account)
    }
    if ($Delivery.to) {
        $baseArgs += @("--to", $Delivery.to)
    }

    $existingId = Resolve-CronJobIdByName -Name $CronSpec.name
    if ($existingId) {
        Write-Log "Cron already exists; updating existing job."
        $null = Invoke-OpenClaw -Arguments (@("cron", "edit", $existingId) + $baseArgs[2..($baseArgs.Count - 1)])
        return $existingId
    }

    $add = Invoke-OpenClaw -Arguments (@("cron", "add", "--json") + $baseArgs) -AllowFailure
    if ($add.ExitCode -eq 0) {
        $addId = Resolve-CronJobIdFromAddOutput -Text $add.Text -ExpectedName $CronSpec.name
        if ($addId) {
            return $addId
        }
    }

    Write-Log "Cron add did not yield a usable job id; attempting cron edit for existing job."
    $existingId = Resolve-CronJobIdByName -Name $CronSpec.name
    if ($existingId) {
        $null = Invoke-OpenClaw -Arguments (@("cron", "edit", $existingId) + $baseArgs[2..($baseArgs.Count - 1)])
        return $existingId
    }
    return $null
}

function Invoke-SetupApi {
    param(
        [string]$Path,
        [hashtable]$Body
    )
    $payload = $Body | ConvertTo-Json -Depth 100
    return Invoke-RestMethod -Uri ($ApiBase.TrimEnd("/") + $Path) -Method Post -ContentType "application/json" -Body $payload
}

$receipt = @{
    openclaw_cli = $null
    openclaw_version = $null
    config_path = $null
    workspace_path = $null
    python_path = $null
    cron_name = "clawborate-patrol"
    plugin_disabled = $false
    dry_run_status = "failed"
    error = $null
}

$manifest = $null
$agentKey = $null

try {
    $script:OpenClawInvocation = @(Resolve-OpenClawInvocation)
    $receipt.openclaw_cli = ($script:OpenClawInvocation -join " ")

    $configPath = Resolve-OpenClawConfigPath
    $receipt.config_path = $configPath
    $root = Resolve-OpenClawRootFromConfig -ConfigPath $configPath
    $script:OpenClawRootPath = $root
    $skillHomePath = Resolve-SkillHome -Root $root
    New-Item -ItemType Directory -Force -Path $skillHomePath | Out-Null

    $script:LogPath = Join-Path $skillHomePath "bootstrap.log"
    Write-Log "Starting Clawborate bootstrap."

    $version = Invoke-OpenClaw -Arguments @("-V") -AllowFailure
    if ($version.Text) {
        $receipt.openclaw_version = $version.Text
    }

    $config = Read-JsonFile -Path $configPath
    $workspacePath = Resolve-WorkspacePath -Config $config -Root $root
    $receipt.workspace_path = $workspacePath

    $legacyPluginConfig = $null
    if ($config -and $config.plugins -and $config.plugins.entries -and $config.plugins.entries.clawborate) {
        $legacyPluginConfig = $config.plugins.entries.clawborate
    }
    $legacyBackup = Backup-LegacyState -SkillHomePath $skillHomePath

    Write-Log "Exchanging setup token."
    $exchange = Invoke-SetupApi -Path "/api/openclaw/setup/exchange" -Body @{ setup_token = $SetupToken }
    if (-not $exchange.install_manifest) {
        throw "Setup exchange did not return install_manifest."
    }
    $manifest = $exchange.install_manifest
    $agentKey = [string]$manifest.agent_key
    if (-not $agentKey) {
        throw "Install manifest did not include agent_key."
    }

    $pythonCommand = Resolve-PythonCommand
    if (-not $pythonCommand) {
        Write-Log "No local Python found; installing private Python runtime."
        $pythonRuntime = @{}
        if ($manifest.python_runtime) {
            $pythonRuntime["url"] = [string]$manifest.python_runtime.url
            $pythonRuntime["sha256"] = [string]$manifest.python_runtime.sha256
        }
        $pythonCommand = Install-PrivatePython -PythonRuntime $pythonRuntime -InstallRoot $skillHomePath
    }
    $receipt.python_path = ($pythonCommand -join " ")

    $downloadsDir = Join-Path $skillHomePath "downloads"
    $bundlePath = Join-Path $downloadsDir "clawborate-skill.zip"
    Write-Log "Downloading skill bundle."
    Download-File -Url ([string]$manifest.skill_bundle.url) -Destination $bundlePath
    $expectedBundleSha = [string]$manifest.skill_bundle.sha256
    if ($expectedBundleSha) {
        $actualBundleSha = Get-FileSha256 -Path $bundlePath
        if ($actualBundleSha -ne $expectedBundleSha.ToLowerInvariant()) {
            throw "Skill bundle sha256 mismatch."
        }
    }

    $extractDir = Join-Path $skillHomePath "installed-skill-temp"
    if (Test-Path $extractDir) {
        Remove-Item -Recurse -Force $extractDir
    }
    Expand-Archive -LiteralPath $bundlePath -DestinationPath $extractDir -Force

    $installedSkillRoot = Join-Path $skillHomePath "installed-skill"
    $sourceSkillDir = Join-Path $extractDir "clawborate-skill"
    if (-not (Test-Path $sourceSkillDir)) {
        throw "Downloaded bundle did not contain clawborate-skill/."
    }
    Sync-Directory -Source $sourceSkillDir -Destination $installedSkillRoot

    $workspaceSkillsDir = Join-Path $workspacePath "skills"
    $workspaceSkillDir = Join-Path $workspaceSkillsDir "clawborate-skill"
    New-Item -ItemType Directory -Force -Path $workspaceSkillsDir | Out-Null
    Sync-Directory -Source $installedSkillRoot -Destination $workspaceSkillDir

    Write-Log "Running skill install."
    $pythonPrefix = Split-CommandPrefix -Prefix $pythonCommand
    & $pythonPrefix.Exe @($pythonPrefix.Args + @(
        (Join-Path $workspaceSkillDir "scripts\install.py"),
        "--agent-key", $agentKey,
        "--skill-home", $skillHomePath,
        "--openclaw-root", $root,
        "--openclaw-cli", ($script:OpenClawInvocation -join " "),
        "--patrol-agent", "main",
        "--patrol-session", "clawborate-patrol",
        "--patrol-every-minutes", "5"
    ))
    if ($LASTEXITCODE -ne 0) {
        throw "Skill install.py failed."
    }

    Merge-LegacyState -SkillHomePath $skillHomePath -BackupPath $legacyBackup -LegacyPluginConfig $legacyPluginConfig

    $configBatchPath = Join-Path $skillHomePath "openclaw-config-set.batch.json"
    Write-JsonFile -Path $configBatchPath -Value $manifest.config_batch
    Invoke-ConfigBatchOrFallback -BatchFile $configBatchPath -Operations $manifest.config_batch
    $receipt.plugin_disabled = $true

    $config = Read-JsonFile -Path $configPath
    $delivery = Resolve-DeliveryTarget -Config $config
    $cronId = Ensure-CronJob -CronSpec @{
        name = "clawborate-patrol"
        agent = "main"
        session = "isolated"
        session_key = "agent:main:clawborate-patrol"
        every = "5m"
        message = "Read CLAWBORATE_PATROL.md and execute one Clawborate patrol tick. If nothing requires user-visible output, reply CLAWBORATE_IDLE."
    } -Delivery $delivery

    $validate = Invoke-OpenClaw -Arguments @("config", "validate", "--json") -AllowFailure
    Write-Log ("OpenClaw config validate output: " + (Format-OpenClawTextForLog -Text $validate.Text))
    if ($cronId) {
        $run = Invoke-OpenClaw -Arguments @("cron", "run", $cronId, "--expect-final") -AllowFailure
        Write-Log ("OpenClaw cron run output: " + (Format-OpenClawTextForLog -Text $run.Text))
    } else {
        Write-Log "OpenClaw cron run skipped: could not resolve cron job id."
    }

    $statePath = Join-Path $skillHomePath "state.json"
    $bootstrapPlanPath = Join-Path $skillHomePath "bootstrap-plan.json"
    if (-not (Test-Path $statePath)) {
        throw "Expected state.json after bootstrap, but it was not found."
    }
    if (-not (Test-Path $bootstrapPlanPath)) {
        throw "Expected bootstrap-plan.json after bootstrap, but it was not found."
    }

    $receipt.dry_run_status = "ok"
    Write-Log "Bootstrap completed successfully."
}
catch {
    $receipt.error = $_.Exception.Message
    Write-Log ("Bootstrap failed: " + $receipt.error)
    throw
}
finally {
    $resultPath = $null
    if ($SkillHome) {
        $resultPath = Join-Path $SkillHome "bootstrap-last-result.json"
    } elseif ($OpenClawRoot) {
        $resultPath = Join-Path $OpenClawRoot "clawborate\bootstrap-last-result.json"
    } else {
        $defaultRoot = Join-Path ([Environment]::GetFolderPath("UserProfile")) ".openclaw"
        $resultPath = Join-Path $defaultRoot "clawborate\bootstrap-last-result.json"
    }
    Write-JsonFile -Path $resultPath -Value $receipt

    if ($manifest -and $manifest.setup_session_id -and $agentKey) {
        try {
            $null = Invoke-SetupApi -Path "/api/openclaw/setup/complete" -Body @{
                setup_session_id = [string]$manifest.setup_session_id
                agent_key = $agentKey
                client_receipt = $receipt
            }
        } catch {
            Write-Log ("Failed to send setup completion receipt: " + $_.Exception.Message)
        }
    }
}
