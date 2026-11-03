$ErrorActionPreference = 'Stop'
$source = Join-Path $PSScriptRoot '..\scripts\local-ai\Start-LocalAI.ps1'
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path $source), [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Syntaxfehler im Starthelfer.' }
$definition = $ast.Find({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Resolve-ModelDirectory'}, $true)
. ([scriptblock]::Create($definition.Extent.Text))
$root = Join-Path ([IO.Path]::GetTempPath()) ('companion-model-test-' + [guid]::NewGuid().ToString('N'))
$directory = Join-Path $root 'models with spaces'
New-Item -ItemType Directory -Path $directory -Force | Out-Null
$pointer = Join-Path $root 'models-location.json'
$passed = 0
function Expect-Rejected {
    try { $null = Resolve-ModelDirectory -Root $root } catch { return }
    throw 'Ein ungueltiger Modellpfad wurde angenommen.'
}
try {
    if ((Resolve-ModelDirectory -Root $root) -ne (Join-Path $root 'models\ollama')) { throw 'Standardpfad falsch.' }; $passed++
    @{modelsPath=$directory} | ConvertTo-Json | Set-Content -LiteralPath $pointer
    if ((Resolve-ModelDirectory -Root $root) -ne $directory) { throw 'Absoluter Pfad falsch.' }; $passed++
    @{modelsPath='models with spaces'} | ConvertTo-Json | Set-Content -LiteralPath $pointer
    if ((Resolve-ModelDirectory -Root $root) -ne $directory) { throw 'Relativer Pfad falsch.' }; $passed++
    @{modelsPath=(Join-Path $root 'missing')} | ConvertTo-Json | Set-Content -LiteralPath $pointer
    Expect-Rejected; $passed++
    @{modelsPath=''} | ConvertTo-Json | Set-Content -LiteralPath $pointer
    Expect-Rejected; $passed++
    '{broken' | Set-Content -LiteralPath $pointer
    Expect-Rejected; $passed++
    [pscustomobject]@{passed=$passed;PowerShell=$PSVersionTable.PSVersion.ToString()} | ConvertTo-Json
} finally {
    # Delete only the two files/directories created above; no recursive deletion.
    Remove-Item -LiteralPath $pointer -ErrorAction SilentlyContinue
    [IO.Directory]::Delete($directory)
    [IO.Directory]::Delete($root)
}
