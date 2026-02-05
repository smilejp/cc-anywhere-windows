#
# CC-Anywhere Hook Script (Windows PowerShell)
#
# This script is called by Claude Code hooks to send events to the
# CC-Anywhere server. It reads the hook payload from stdin and posts
# it to the /api/hooks endpoint.
#
# Environment variables:
#   CC_ANYWHERE_URL - Base URL of the CC-Anywhere server (default: http://localhost:8080)
#
# The script runs asynchronously to avoid blocking Claude Code.
#

param(
    [Parameter(Position=0)]
    [string]$EventType = ""
)

# Get server URL from environment or use default
$ServerUrl = if ($env:CC_ANYWHERE_URL) { $env:CC_ANYWHERE_URL } else { "http://localhost:8080" }

# Get the event type from argument or environment variable
if (-not $EventType) {
    $EventType = if ($env:CLAUDE_HOOK_EVENT_NAME) { $env:CLAUDE_HOOK_EVENT_NAME } else { "unknown" }
}

# Read stdin (the hook payload)
$Input = [Console]::In.ReadToEnd()

# If no input, exit silently
if ([string]::IsNullOrWhiteSpace($Input)) {
    exit 0
}

# Create the request payload with event type
$Payload = @{
    event_type = $EventType
    payload = $Input | ConvertFrom-Json
} | ConvertTo-Json -Depth 10

# Send to CC-Anywhere server asynchronously
# Using Start-Job to run in background and not block Claude Code
Start-Job -ScriptBlock {
    param($Url, $Body)
    try {
        Invoke-RestMethod -Uri $Url -Method Post -ContentType "application/json" -Body $Body -TimeoutSec 3 | Out-Null
    } catch {
        # Silently ignore errors
    }
} -ArgumentList "${ServerUrl}/api/hooks", $Payload | Out-Null

# Exit immediately (don't wait for the job)
exit 0
