param([string]$Mode, [string]$Payload)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName System.Speech
$speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    if ($Mode -eq 'voices') {
        $voices = @($speaker.GetInstalledVoices() | Where-Object Enabled | ForEach-Object {
            @{id=$_.VoiceInfo.Name;name=$_.VoiceInfo.Name;language=$_.VoiceInfo.Culture.Name;engine='windows';installed=$true}
        })
        ConvertTo-Json -InputObject $voices -Compress
    } elseif ($Mode -eq 'synthesize') {
        $inputData = Get-Content -LiteralPath $Payload -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($inputData.voice) { $speaker.SelectVoice([string]$inputData.voice) }
        $speaker.Rate = [Math]::Max(-10, [Math]::Min(10, [int][Math]::Round(([double]$inputData.rate - 1) * 5)))
        $speaker.SetOutputToWaveFile([string]$inputData.output)
        $speaker.Speak([string]$inputData.text)
        $speaker.SetOutputToNull()
    } else { throw 'Unbekannte Sprachaufgabe.' }
} finally { $speaker.Dispose() }
