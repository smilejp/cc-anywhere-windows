"""CC-Anywhere Windows main entry point."""

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

import uvicorn
import yaml
from dotenv import load_dotenv

from .adapters.discord_bot import run_bot as run_discord_bot
from .adapters.web import app, set_session_manager
from .core import SessionManager
from .core.hook_config import install_hooks, uninstall_hooks, get_hook_status

# Load environment variables from .env file
load_dotenv()

logger = logging.getLogger(__name__)


def load_config(config_path: Path | None = None) -> dict:
    """Load configuration from YAML file."""
    if config_path is None:
        # Try multiple locations
        locations = [
            Path(__file__).parent.parent.parent / "config" / "config.yaml",
            Path.home() / ".cc-anywhere" / "config.yaml",
            Path("config.yaml"),
        ]
        for loc in locations:
            if loc.exists():
                config_path = loc
                break

    if config_path is None or not config_path.exists():
        logger.warning("No config file found, using defaults")
        return {}

    with open(config_path) as f:
        return yaml.safe_load(f) or {}


def setup_logging(config: dict) -> None:
    """Set up logging configuration."""
    log_config = config.get("logging", {})
    level = getattr(logging, log_config.get("level", "INFO").upper())

    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Set third-party loggers to WARNING
    logging.getLogger("uvicorn").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)


async def run_server(config: dict) -> None:
    """Run the CC-Anywhere server."""
    # Extract config values
    server_config = config.get("server", {})
    host = server_config.get("host", "0.0.0.0")
    port = server_config.get("port", 8080)

    # SSL configuration
    ssl_config = server_config.get("ssl", {})
    ssl_enabled = ssl_config.get("enabled", False)
    ssl_keyfile = None
    ssl_certfile = None

    if ssl_enabled:
        ssl_keyfile = ssl_config.get("keyfile")
        ssl_certfile = ssl_config.get("certfile")

        # Resolve relative paths
        if ssl_keyfile and not Path(ssl_keyfile).is_absolute():
            ssl_keyfile = str(Path(__file__).parent.parent.parent / ssl_keyfile)
        if ssl_certfile and not Path(ssl_certfile).is_absolute():
            ssl_certfile = str(Path(__file__).parent.parent.parent / ssl_certfile)

        # Verify files exist
        if not Path(ssl_keyfile).exists():
            logger.error(f"SSL key file not found: {ssl_keyfile}")
            logger.info("Generate with: mkcert -key-file certs/key.pem -cert-file certs/cert.pem localhost")
            raise FileNotFoundError(f"SSL key file not found: {ssl_keyfile}")
        if not Path(ssl_certfile).exists():
            logger.error(f"SSL certificate file not found: {ssl_certfile}")
            raise FileNotFoundError(f"SSL certificate file not found: {ssl_certfile}")

    claude_config = config.get("claude", {})
    sessions_config = config.get("sessions", {})

    # Create session manager
    manager = SessionManager(
        claude_command=claude_config.get("command", "claude"),
        claude_args=claude_config.get("args", ["--dangerously-skip-permissions"]),
        default_working_dir=claude_config.get("default_working_dir", "~"),
        max_sessions=sessions_config.get("max_sessions", 10),
    )

    # Set session manager for web app
    set_session_manager(manager)

    # Run uvicorn
    uvicorn_config = uvicorn.Config(
        app=app,
        host=host,
        port=port,
        log_level="warning",
        ssl_keyfile=ssl_keyfile,
        ssl_certfile=ssl_certfile,
    )
    server = uvicorn.Server(uvicorn_config)

    protocol = "https" if ssl_enabled else "http"
    logger.info(f"Starting CC-Anywhere Windows server on {protocol}://{host}:{port}")

    # Build list of tasks to run
    running_tasks: list[asyncio.Task] = []
    running_tasks.append(asyncio.create_task(server.serve(), name="uvicorn"))

    # Add Discord bot if enabled
    discord_config = config.get("discord", {})
    if discord_config.get("enabled", False):
        discord_token = os.getenv("DISCORD_BOT_TOKEN")
        if discord_token:
            allowed_users = discord_config.get("allowed_user_ids")
            allowed_channels = discord_config.get("allowed_channel_ids")
            logger.info("Starting Discord bot...")
            running_tasks.append(
                asyncio.create_task(
                    run_discord_bot(
                        discord_token, manager, allowed_users, allowed_channels
                    ),
                    name="discord",
                )
            )
        else:
            logger.warning("Discord enabled but DISCORD_BOT_TOKEN not set")

    # Set up shutdown handler
    def signal_handler():
        logger.info("Shutdown signal received, cancelling tasks...")
        for task in running_tasks:
            if not task.done():
                task.cancel()
        # Also tell uvicorn to shutdown
        server.should_exit = True

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass

    try:
        await asyncio.gather(*running_tasks, return_exceptions=True)
    except asyncio.CancelledError:
        pass
    finally:
        logger.info("Shutting down session manager...")
        await manager.shutdown()


def handle_ssl_command(args: list[str]) -> int:
    """Handle ssl subcommand for certificate management.

    Usage:
        python -m cc_anywhere ssl setup          # mkcert 사용 (권장)
        python -m cc_anywhere ssl setup --self-signed  # 자체 서명 인증서
        python -m cc_anywhere ssl status         # 인증서 상태 확인
    """
    import shutil
    import subprocess

    certs_dir = Path(__file__).parent.parent.parent / "certs"

    if not args:
        print("Usage: python -m cc_anywhere ssl <setup|status>")
        print("Options for 'setup':")
        print("  --self-signed    Use self-signed certificate instead of mkcert")
        return 1

    subcommand = args[0]

    if subcommand == "setup":
        use_self_signed = "--self-signed" in args

        # Create certs directory
        certs_dir.mkdir(exist_ok=True)
        key_path = certs_dir / "key.pem"
        cert_path = certs_dir / "cert.pem"

        if use_self_signed:
            print("Generating self-signed certificate...")
            print("Warning: Browser will show security warnings.")
            print()

            try:
                subprocess.run([
                    "openssl", "req", "-x509", "-newkey", "rsa:4096",
                    "-keyout", str(key_path),
                    "-out", str(cert_path),
                    "-days", "365", "-nodes",
                    "-subj", "/CN=localhost"
                ], check=True)
                print(f"Certificate generated!")
                print(f"   Key: {key_path}")
                print(f"   Cert: {cert_path}")
            except FileNotFoundError:
                print("Error: openssl is not installed.")
                print("   Install OpenSSL or use mkcert instead.")
                return 1
            except subprocess.CalledProcessError as e:
                print(f"Error: Certificate generation failed: {e}")
                return 1
        else:
            # Use mkcert
            if not shutil.which("mkcert"):
                print("Error: mkcert is not installed.")
                print()
                print("Install instructions:")
                print("  Windows: choco install mkcert")
                print("  Or download from: https://github.com/FiloSottile/mkcert/releases")
                print()
                print("After installing:")
                print("  mkcert -install")
                print()
                print("Or use --self-signed option.")
                return 1

            print("Generating certificate with mkcert...")
            try:
                subprocess.run([
                    "mkcert",
                    "-key-file", str(key_path),
                    "-cert-file", str(cert_path),
                    "localhost", "127.0.0.1"
                ], check=True)
                print()
                print(f"Certificate generated!")
                print(f"   Key: {key_path}")
                print(f"   Cert: {cert_path}")
            except subprocess.CalledProcessError as e:
                print(f"Error: Certificate generation failed: {e}")
                return 1

        print()
        print("Next steps:")
        print("  1. Set ssl.enabled: true in config/config.yaml")
        print("  2. Run: python -m cc_anywhere")
        print("  3. Visit: https://localhost:8080")
        return 0

    elif subcommand == "status":
        key_path = certs_dir / "key.pem"
        cert_path = certs_dir / "cert.pem"

        print("SSL Certificate Status:")
        print()

        if key_path.exists() and cert_path.exists():
            print(f"Certificate exists")
            print(f"   Key: {key_path}")
            print(f"   Cert: {cert_path}")

            # Check certificate info
            try:
                result = subprocess.run(
                    ["openssl", "x509", "-in", str(cert_path), "-noout", "-dates", "-subject"],
                    capture_output=True, text=True
                )
                if result.returncode == 0:
                    print()
                    print("Certificate info:")
                    for line in result.stdout.strip().split("\n"):
                        print(f"   {line}")
            except Exception:
                pass
        else:
            print("No certificate found")
            print()
            print("Run: python -m cc_anywhere ssl setup")

        return 0

    else:
        print(f"Unknown ssl subcommand: {subcommand}")
        return 1


def handle_hooks_command(args: list[str]) -> int:
    """Handle hooks subcommand.

    Usage:
        python -m cc_anywhere hooks install [--url URL]
        python -m cc_anywhere hooks uninstall
        python -m cc_anywhere hooks status
    """
    if not args:
        print("Usage: python -m cc_anywhere hooks <install|uninstall|status>")
        return 1

    subcommand = args[0]

    if subcommand == "install":
        # Parse optional --url argument
        server_url = "http://localhost:8080"
        if len(args) >= 3 and args[1] == "--url":
            server_url = args[2]

        print(f"Installing CC-Anywhere hooks...")
        print(f"Server URL: {server_url}")

        if install_hooks(server_url):
            print("Hooks installed successfully!")
            print("\nClaude Code will now send notifications to CC-Anywhere.")
            print("Make sure the server is running at the configured URL.")
            return 0
        else:
            print("Failed to install hooks.")
            return 1

    elif subcommand == "uninstall":
        print("Uninstalling CC-Anywhere hooks...")

        if uninstall_hooks():
            print("Hooks uninstalled successfully!")
            return 0
        else:
            print("Failed to uninstall hooks.")
            return 1

    elif subcommand == "status":
        status = get_hook_status()

        if status["installed"]:
            print("CC-Anywhere hooks are installed")
            print(f"   Server URL: {status['server_url']}")
            print(f"   Events: {', '.join(status['events'])}")
        else:
            print("CC-Anywhere hooks are not installed")
            print("\nRun 'python -m cc_anywhere hooks install' to install.")
        return 0

    else:
        print(f"Unknown hooks subcommand: {subcommand}")
        print("Usage: python -m cc_anywhere hooks <install|uninstall|status>")
        return 1


def print_help() -> None:
    """Print help message."""
    print("""CC-Anywhere Windows - Claude Code Remote Access (WezTerm backend)

Usage:
    python -m cc_anywhere              Start the server
    python -m cc_anywhere hooks        Manage Claude Code hooks
    python -m cc_anywhere ssl          Manage SSL certificates

Commands:
    (default)           Start the CC-Anywhere server
    hooks install       Install hooks into Claude Code settings
    hooks uninstall     Remove hooks from Claude Code settings
    hooks status        Check if hooks are installed
    ssl setup           Generate SSL certificates (mkcert recommended)
    ssl setup --self-signed  Generate self-signed certificate
    ssl status          Check SSL certificate status

Options for 'hooks install':
    --url URL           Server URL (default: http://localhost:8080)

Requirements:
    - WezTerm must be installed and available in PATH
    - Install: winget install wez.wezterm

Examples:
    python -m cc_anywhere
    python -m cc_anywhere hooks install
    python -m cc_anywhere hooks install --url https://myserver:8080
    python -m cc_anywhere ssl setup
    python -m cc_anywhere ssl status
""")


def main() -> None:
    """Main entry point."""
    args = sys.argv[1:]

    # Handle help
    if args and args[0] in ("-h", "--help", "help"):
        print_help()
        return

    # Handle hooks subcommand
    if args and args[0] == "hooks":
        sys.exit(handle_hooks_command(args[1:]))

    # Handle ssl subcommand
    if args and args[0] == "ssl":
        sys.exit(handle_ssl_command(args[1:]))

    # Default: run server
    config = load_config()
    setup_logging(config)

    logger.info("CC-Anywhere Windows starting...")
    logger.info(f"Python version: {__import__('sys').version}")

    try:
        asyncio.run(run_server(config))
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        raise


if __name__ == "__main__":
    main()
