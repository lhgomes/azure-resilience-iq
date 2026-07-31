param(
    [Parameter(Mandatory = $true)]
    [string]$StorageAccountName,
    [Parameter(Mandatory = $true)]
    [string]$ContainerName,
    [Parameter(Mandatory = $true)]
    [string]$PackageBlobName,
    [string]$LocalPackagePath = "",
    [Parameter(Mandatory = $true)]
    [string]$FoundryProjectEndpoint,
    [Parameter(Mandatory = $true)]
    [string]$SearchEndpoint,
    [Parameter(Mandatory = $true)]
    [string]$ReasoningModel,
    [Parameter(Mandatory = $true)]
    [string]$EmbeddingModel,
    [Parameter(Mandatory = $true)]
    [string]$EmbeddingEndpoint,
    [string]$OpenAiApiVersion = "2025-03-01-preview",
    [string]$DeploymentStatePrefix = "",
    [string]$AgentsMigrate = "false",
    [string]$AppOnly = "false"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$appRoot = "C:\AzureResilienceIQ"
$backendRoot = Join-Path $appRoot "backend"
$frontendRoot = Join-Path $appRoot "frontend"
$tempRoot = "C:\AzureResilienceIQ-Deploy"
$packagePath = Join-Path $tempRoot "application.zip"
$serviceRoot = Join-Path $appRoot "service"
$serviceExe = Join-Path $serviceRoot "AzureResilienceIQService.exe"
$serviceConfig = Join-Path $serviceRoot "AzureResilienceIQService.xml"
$serviceId = "AzureResilienceIQ"

function Invoke-Download {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][string]$OutFile
    )

    Write-Host "Downloading $Uri"
    Invoke-WebRequest -Uri $Uri -OutFile $OutFile -UseBasicParsing
}

function Install-Executable {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][string]$FileName,
        [Parameter(Mandatory = $true)][string]$Arguments
    )

    $installerPath = Join-Path $tempRoot $FileName
    Invoke-Download -Uri $Uri -OutFile $installerPath
    if ([IO.Path]::GetExtension($installerPath) -eq ".msi") {
        $process = Start-Process `
            -FilePath "msiexec.exe" `
            -ArgumentList "/i `"$installerPath`" $Arguments" `
            -Wait `
            -PassThru
    } else {
        $process = Start-Process -FilePath $installerPath -ArgumentList $Arguments -Wait -PassThru
    }
    if ($process.ExitCode -notin @(0, 1641, 3010)) {
        throw "Installer $FileName failed with exit code $($process.ExitCode)"
    }
}

New-Item -ItemType Directory -Path $tempRoot -Force | Out-Null
New-ItemProperty `
    -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" `
    -Name "LongPathsEnabled" `
    -Value 1 `
    -PropertyType DWord `
    -Force | Out-Null

$pythonExe = "C:\Program Files\Python312\python.exe"
if (-not (Test-Path $pythonExe)) {
    Install-Executable `
        -Uri "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe" `
        -FileName "python-3.12.10-amd64.exe" `
        -Arguments "/quiet InstallAllUsers=1 PrependPath=1 Include_test=0 TargetDir=`"C:\Program Files\Python312`""
}

$nodeExe = "C:\Program Files\nodejs\node.exe"
$nodeMajor = 0
if (Test-Path $nodeExe) {
    $nodeMajor = [int]((& $nodeExe --version).TrimStart("v").Split(".")[0])
}
if ($nodeMajor -lt 20) {
    Install-Executable `
        -Uri "https://nodejs.org/dist/v20.19.4/node-v20.19.4-x64.msi" `
        -FileName "node-v20.19.4-x64.msi" `
        -Arguments "/quiet /norestart"
}

$gitExe = "C:\Program Files\Git\cmd\git.exe"
if (-not (Test-Path $gitExe)) {
    Install-Executable `
        -Uri "https://github.com/git-for-windows/git/releases/download/v2.49.0.windows.1/Git-2.49.0-64-bit.exe" `
        -FileName "Git-2.49.0-64-bit.exe" `
        -Arguments "/VERYSILENT /NORESTART /NOCANCEL /SP-"
}

$env:Path = "C:\Program Files\Python312;C:\Program Files\Python312\Scripts;C:\Program Files\nodejs;C:\Program Files\Git\cmd;$env:Path"

$tokenResponse = Invoke-RestMethod `
    -Headers @{ Metadata = "true" } `
    -Method GET `
    -Uri "http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https%3A%2F%2Fstorage.azure.com%2F"
$storageHeaders = @{
    Authorization  = "Bearer $($tokenResponse.access_token)"
    "x-ms-version" = "2023-11-03"
}
$containerUrl = "https://$StorageAccountName.blob.core.windows.net/${ContainerName}?restype=container"
try {
    Invoke-WebRequest -Method Put -Uri $containerUrl -Headers $storageHeaders -UseBasicParsing | Out-Null
} catch {
    if ($_.Exception.Response.StatusCode.value__ -ne 409) {
        throw
    }
}

if ($LocalPackagePath) {
    Write-Host "Using application package transferred through the private access path"
    if ([IO.Path]::GetFullPath($LocalPackagePath) -ne [IO.Path]::GetFullPath($packagePath)) {
        Copy-Item -Path $LocalPackagePath -Destination $packagePath -Force
    }
} else {
    Write-Host "Downloading application package through the VM managed identity"
    $blobUrl = "https://$StorageAccountName.blob.core.windows.net/$ContainerName/$PackageBlobName"
    Invoke-WebRequest `
        -Uri $blobUrl `
        -Headers $storageHeaders `
        -OutFile $packagePath `
        -UseBasicParsing
}

$persistRoot = Join-Path $tempRoot "persist"
Remove-Item $persistRoot -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $persistRoot -Force | Out-Null

$dataPath = Join-Path $backendRoot "data"
if (Test-Path $dataPath) {
    Move-Item $dataPath (Join-Path $persistRoot "data") -Force
}

if (Test-Path $serviceExe) {
    & $serviceExe stop | Out-Null
    & $serviceExe uninstall | Out-Null
}

Remove-Item $appRoot -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $appRoot -Force | Out-Null
Expand-Archive -Path $packagePath -DestinationPath $appRoot -Force

if (Test-Path (Join-Path $persistRoot "data")) {
    Move-Item (Join-Path $persistRoot "data") $dataPath -Force
} else {
    New-Item -ItemType Directory -Path $dataPath -Force | Out-Null
}

Write-Host "Installing backend dependencies"
$venvRoot = Join-Path $backendRoot ".venv"
& $pythonExe -m venv $venvRoot
$venvPython = Join-Path $venvRoot "Scripts\python.exe"
& $venvPython -m pip install --disable-pip-version-check --upgrade pip
& $venvPython -m pip install --disable-pip-version-check -r (Join-Path $backendRoot "requirements.txt")
$env:PYTHONPATH = $backendRoot

if (($AppOnly -ne "true") -or ($AgentsMigrate -eq "true")) {
    Push-Location $backendRoot
    try {
    Write-Host "Provisioning Foundry agents and Azure AI Search indexes"
    $provisionArgs = @(
        (Join-Path $backendRoot "deploy\scripts\provision_foundry_assets.py"),
        "--foundry-project-endpoint", $FoundryProjectEndpoint,
        "--reasoning-model", $ReasoningModel,
        "--search-endpoint", $SearchEndpoint,
        "--embedding-endpoint", $EmbeddingEndpoint,
        "--embedding-deployment", $EmbeddingModel,
        "--embedding-model", $EmbeddingModel
    )
    if ($AgentsMigrate -eq "true") {
        $provisionArgs += "--recreate-existing"
    }
    & $venvPython @provisionArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Foundry and Search provisioning failed with exit code $LASTEXITCODE"
    }

    $ragRoot = Join-Path $backendRoot "agent\rag"
    $ragStage = Join-Path $tempRoot "rag-refresh"
    Remove-Item $ragStage -Recurse -Force -ErrorAction SilentlyContinue
    & $venvPython -m app.tools.bootstrap_rag_sources `
        --repo-root $appRoot `
        --base-rag-dir $ragStage `
        --refresh-clone
    if ($LASTEXITCODE -eq 0) {
        Remove-Item $ragRoot -Recurse -Force -ErrorAction SilentlyContinue
        New-Item -ItemType Directory -Path (Split-Path $ragRoot) -Force | Out-Null
        Move-Item $ragStage $ragRoot -Force
    } else {
        Write-Warning "RAG source refresh failed; using packaged RAG content"
    }

    $terraformDocs = Join-Path $ragRoot "terraform\docs"
    New-Item -ItemType Directory -Path $terraformDocs -Force | Out-Null
    $ingestArgs = @(
        "-m", "app.llm.ingest_search",
        "--targets", "aprl,terraform",
        "--include-glob", "**/*",
        "--aprl-local-path", (Join-Path $backendRoot "aprl\docs"),
        "--aprl-local-path", (Join-Path $backendRoot "aprl\azure-resources"),
        "--terraform-local-path", $terraformDocs,
        "--aprl-index-name", "learn-aprl-index",
        "--terraform-index-name", "learn-terraform-hybrid-index",
        "--search-endpoint", $SearchEndpoint,
        "--embedding-model", $EmbeddingModel
    )
    $terraformUrls = Join-Path $ragRoot "terraform\terraform_urls.txt"
    if (Test-Path $terraformUrls) {
        $ingestArgs += @("--terraform-url-file", $terraformUrls)
    }
    $env:AI_FOUNDRY_PROJECT_ENDPOINT = $FoundryProjectEndpoint
    $env:AI_FOUNDRY_OPENAI_API_VERSION = $OpenAiApiVersion
    & $venvPython @ingestArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Azure AI Search hydration failed with exit code $LASTEXITCODE"
    }
    } finally {
        Pop-Location
    }
}

Write-Host "Building React frontend"
Push-Location $frontendRoot
try {
    & "C:\Program Files\nodejs\npm.cmd" ci
    if ($LASTEXITCODE -ne 0) {
        throw "npm ci failed with exit code $LASTEXITCODE"
    }
    & "C:\Program Files\nodejs\npm.cmd" run build
    if ($LASTEXITCODE -ne 0) {
        throw "npm run build failed with exit code $LASTEXITCODE"
    }
} finally {
    Pop-Location
}

$dataStorageBackend = if ($StorageAccountName -and $ContainerName) { "blob" } else { "local" }
$environmentLines = @(
    "AI_FOUNDRY_PROJECT_ENDPOINT=$FoundryProjectEndpoint",
    "AI_FOUNDRY_OPENAI_API_VERSION=$OpenAiApiVersion",
    "AI_FOUNDRY_REASONING_MODEL=$ReasoningModel",
    "AI_FOUNDRY_EMBEDDING_MODEL=$EmbeddingModel",
    "AI_FOUNDRY_CHAT_AGENT_REFERENCE=chat-agent",
    "AI_FOUNDRY_RESILIENCE_AGENT_REFERENCE=resilience-agent",
    "AI_FOUNDRY_ANNOTATIONS_AGENT_REFERENCE=annotations-agent",
    "AI_FOUNDRY_TERRAFORM_AGENT_REFERENCE=terraform-compiler-agent",
    "AZURE_SEARCH_ENDPOINT=$SearchEndpoint",
    "AZURE_SEARCH_INDEX_NAME_APRL=learn-aprl-index",
    "AZURE_SEARCH_INDEX_NAME_TERRAFORM=learn-terraform-hybrid-index",
    "DATA_STORAGE_BACKEND=$dataStorageBackend",
    "DATA_STORAGE_ACCOUNT=$StorageAccountName",
    "DATA_STORAGE_CONTAINER=$ContainerName",
    "DATA_STORAGE_PREFIX=$DeploymentStatePrefix",
    "FRONTEND_DIST_DIR=$frontendRoot\dist",
    "CORS_ALLOWED_ORIGINS=http://localhost,http://127.0.0.1"
)
Set-Content -Path (Join-Path $backendRoot ".env") -Value $environmentLines -Encoding ASCII

Write-Host "Installing Azure Resiliency IQ Windows service"
New-Item -ItemType Directory -Path $serviceRoot -Force | Out-Null
Invoke-Download `
    -Uri "https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW-x64.exe" `
    -OutFile $serviceExe

$serviceXml = @"
<service>
  <id>$serviceId</id>
  <name>Azure Resiliency IQ</name>
  <description>Azure Resiliency IQ FastAPI backend and React frontend</description>
  <executable>$venvPython</executable>
  <arguments>-m uvicorn app.main:app --host 127.0.0.1 --port 80</arguments>
  <workingdirectory>$backendRoot</workingdirectory>
    <env name="PYTHONUTF8" value="1" />
    <env name="PYTHONIOENCODING" value="utf-8" />
  <logpath>$serviceRoot\logs</logpath>
  <log mode="roll-by-size">
    <sizeThreshold>10240</sizeThreshold>
    <keepFiles>8</keepFiles>
  </log>
  <onfailure action="restart" delay="10 sec" />
  <stoptimeout>15 sec</stoptimeout>
</service>
"@
Set-Content -Path $serviceConfig -Value $serviceXml -Encoding UTF8

& $serviceExe install
if ($LASTEXITCODE -ne 0) {
    throw "Windows service installation failed with exit code $LASTEXITCODE"
}
& $serviceExe start
if ($LASTEXITCODE -ne 0) {
    throw "Windows service startup failed with exit code $LASTEXITCODE"
}

$healthy = $false
foreach ($attempt in 1..30) {
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1/health" -UseBasicParsing -TimeoutSec 5
        if ($response.StatusCode -eq 200) {
            $healthy = $true
            break
        }
    } catch {
        Start-Sleep -Seconds 2
    }
}
if (-not $healthy) {
    throw "Azure Resiliency IQ did not become healthy at http://127.0.0.1/health"
}

Write-Host "Azure Resiliency IQ is available at http://localhost"