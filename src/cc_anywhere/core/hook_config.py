"""Claude Code hook configuration and installation utilities."""

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Default CC-Anywhere server URL
DEFAULT_SERVER_URL = "http://localhost:8080"

# Claude Code settings directory
CLAUDE_CODE_DIR = Path.home() / ".claude"
SETTINGS_FILE = CLAUDE_CODE_DIR / "settings.json"

# Hook script location (installed with cc-anywhere package)
HOOK_SCRIPT_NAME = "cc-hook.ps1"  # PowerShell for Windows


def get_hook_script_path() -> Path:
    """Get the path to the hook script.

    The script is located in the scripts directory of the package.
    """
    # Try to find in package scripts directory
    package_dir = Path(__file__).parent.parent.parent.parent
    script_path = package_dir / "scripts" / HOOK_SCRIPT_NAME

    if script_path.exists():
        return script_path

    # Fallback: try current working directory
    cwd_script = Path.cwd() / "scripts" / HOOK_SCRIPT_NAME
    if cwd_script.exists():
        return cwd_script

    raise FileNotFoundError(f"Hook script not found: {HOOK_SCRIPT_NAME}")


def generate_hook_config(server_url: str = DEFAULT_SERVER_URL) -> dict:
    """Generate Claude Code hook configuration.

    Args:
        server_url: URL of the CC-Anywhere server

    Returns:
        Hook configuration dictionary for settings.json
    """
    try:
        script_path = get_hook_script_path()
        script_cmd = f"powershell -ExecutionPolicy Bypass -File \"{script_path.absolute()}\""
    except FileNotFoundError:
        # Fallback to relative path if script not found
        script_cmd = "powershell -ExecutionPolicy Bypass -File \"./scripts/cc-hook.ps1\""
        logger.warning(f"Hook script not found, using relative path: {script_cmd}")

    return {
        "hooks": {
            # Stop event - task completed
            "Stop": [
                {
                    "matcher": "",  # Match all
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"{script_cmd} Stop",
                            "timeout": 5000,
                        }
                    ],
                }
            ],
            # PostToolUseFailure - tool execution failed
            "PostToolUseFailure": [
                {
                    "matcher": "",
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"{script_cmd} PostToolUseFailure",
                            "timeout": 5000,
                        }
                    ],
                }
            ],
            # Notification - permission request or idle
            "Notification": [
                {
                    "matcher": "",
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"{script_cmd} Notification",
                            "timeout": 5000,
                        }
                    ],
                }
            ],
        },
        # Environment variable for hook script
        "env": {
            "CC_ANYWHERE_URL": server_url,
        },
    }


def load_settings() -> dict:
    """Load existing Claude Code settings."""
    if not SETTINGS_FILE.exists():
        return {}

    try:
        with open(SETTINGS_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"Failed to load settings: {e}")
        return {}


def save_settings(settings: dict) -> bool:
    """Save Claude Code settings.

    Args:
        settings: Settings dictionary to save

    Returns:
        True if successful, False otherwise
    """
    # Ensure directory exists
    CLAUDE_CODE_DIR.mkdir(parents=True, exist_ok=True)

    # Backup existing settings
    if SETTINGS_FILE.exists():
        backup_path = SETTINGS_FILE.with_suffix(".json.bak")
        shutil.copy2(SETTINGS_FILE, backup_path)
        logger.info(f"Backed up existing settings to {backup_path}")

    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump(settings, f, indent=2)
        logger.info(f"Saved settings to {SETTINGS_FILE}")
        return True
    except IOError as e:
        logger.error(f"Failed to save settings: {e}")
        return False


def install_hooks(server_url: str = DEFAULT_SERVER_URL) -> bool:
    """Install CC-Anywhere hooks into Claude Code settings.

    This merges hook configuration into existing settings without
    overwriting other settings.

    Args:
        server_url: URL of the CC-Anywhere server

    Returns:
        True if successful, False otherwise
    """
    # Load existing settings
    settings = load_settings()

    # Generate hook config
    hook_config = generate_hook_config(server_url)

    # Merge hooks
    if "hooks" not in settings:
        settings["hooks"] = {}

    for event_type, matchers in hook_config["hooks"].items():
        if event_type not in settings["hooks"]:
            settings["hooks"][event_type] = []

        # Check if our hook is already installed
        existing_cmds = set()
        for matcher in settings["hooks"][event_type]:
            for hook in matcher.get("hooks", []):
                if hook.get("type") == "command":
                    existing_cmds.add(hook.get("command", ""))

        # Add our matchers if not already present
        for matcher in matchers:
            for hook in matcher.get("hooks", []):
                cmd = hook.get("command", "")
                if cmd and cmd not in existing_cmds:
                    settings["hooks"][event_type].append(matcher)
                    logger.info(f"Added hook for {event_type}: {cmd}")

    # Merge environment variables
    if "env" not in settings:
        settings["env"] = {}
    settings["env"].update(hook_config.get("env", {}))

    return save_settings(settings)


def uninstall_hooks() -> bool:
    """Remove CC-Anywhere hooks from Claude Code settings.

    Returns:
        True if successful, False otherwise
    """
    settings = load_settings()
    if not settings:
        return True  # Nothing to uninstall

    modified = False

    # Remove our hooks
    if "hooks" in settings:
        for event_type in ["Stop", "PostToolUseFailure", "Notification"]:
            if event_type in settings["hooks"]:
                original_len = len(settings["hooks"][event_type])
                settings["hooks"][event_type] = [
                    matcher
                    for matcher in settings["hooks"][event_type]
                    if not any(
                        "cc-hook" in hook.get("command", "")
                        for hook in matcher.get("hooks", [])
                    )
                ]
                if len(settings["hooks"][event_type]) < original_len:
                    modified = True
                    logger.info(f"Removed hooks for {event_type}")

    # Remove our environment variable
    if "env" in settings and "CC_ANYWHERE_URL" in settings["env"]:
        del settings["env"]["CC_ANYWHERE_URL"]
        modified = True
        logger.info("Removed CC_ANYWHERE_URL from environment")

    if modified:
        return save_settings(settings)

    return True


def get_hook_status() -> dict:
    """Check if hooks are installed and their configuration.

    Returns:
        Dictionary with hook status information
    """
    settings = load_settings()

    status = {
        "installed": False,
        "server_url": None,
        "events": [],
    }

    if "env" in settings:
        status["server_url"] = settings["env"].get("CC_ANYWHERE_URL")

    if "hooks" in settings:
        for event_type in ["Stop", "PostToolUseFailure", "Notification"]:
            if event_type in settings["hooks"]:
                for matcher in settings["hooks"][event_type]:
                    for hook in matcher.get("hooks", []):
                        if "cc-hook" in hook.get("command", ""):
                            status["installed"] = True
                            status["events"].append(event_type)
                            break

    return status
