"""Git utilities for worktree management."""

import logging
import os
import re
import subprocess
import uuid
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def is_git_repo(path: str) -> bool:
    """Check if a path is inside a Git repository.

    Args:
        path: Directory path to check

    Returns:
        True if inside a Git repository
    """
    expanded_path = os.path.expanduser(path)
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=expanded_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0 and result.stdout.strip() == "true"
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return False


def get_git_root(path: str) -> Optional[str]:
    """Get the root directory of a Git repository.

    Args:
        path: Directory path inside the repository

    Returns:
        Repository root path, or None if not a Git repo
    """
    expanded_path = os.path.expanduser(path)
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=expanded_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return None
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return None


def get_current_branch(repo_path: str) -> Optional[str]:
    """Get the current branch name.

    Args:
        repo_path: Path to the repository

    Returns:
        Current branch name, or None if error
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return None
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return None


def generate_branch_name(session_name: str) -> str:
    """Generate a unique branch name for a session.

    Converts session name to a valid Git branch name with cc/ prefix.

    Args:
        session_name: Human-readable session name

    Returns:
        Branch name like "cc/my-feature-a1b2c3"
    """
    # Normalize the session name
    # - Convert to lowercase
    # - Replace spaces and underscores with hyphens
    # - Remove special characters except hyphens
    # - Remove consecutive hyphens
    slug = session_name.lower()
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"[^a-z0-9-]", "", slug)
    slug = re.sub(r"-+", "-", slug)
    slug = slug.strip("-")

    # Add short UUID for uniqueness
    short_uuid = uuid.uuid4().hex[:6]

    return f"cc/{slug}-{short_uuid}"


def get_worktree_path(git_root: str, branch_name: str) -> str:
    """Get the path where worktree should be created.

    Worktrees are stored in {git_root}/.worktrees/{branch_name_slug}/

    Args:
        git_root: Git repository root path
        branch_name: Branch name (e.g., "cc/my-feature-a1b2")

    Returns:
        Full path for the worktree
    """
    # Convert branch name to directory name (remove cc/ prefix, keep rest)
    dir_name = branch_name.replace("/", "-")
    return os.path.join(git_root, ".worktrees", dir_name)


def list_worktrees(repo_path: str) -> list[dict]:
    """List all worktrees in a repository.

    Args:
        repo_path: Path to the repository

    Returns:
        List of worktree info dicts with 'path', 'branch', 'head'
    """
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return []

        worktrees = []
        current = {}

        for line in result.stdout.split("\n"):
            if line.startswith("worktree "):
                if current:
                    worktrees.append(current)
                current = {"path": line[9:]}
            elif line.startswith("HEAD "):
                current["head"] = line[5:]
            elif line.startswith("branch refs/heads/"):
                current["branch"] = line[18:]
            elif line == "":
                if current:
                    worktrees.append(current)
                    current = {}

        return worktrees

    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return []


def create_worktree(
    repo_path: str,
    branch_name: str,
    worktree_path: Optional[str] = None,
    base_branch: Optional[str] = None,
) -> Tuple[bool, str]:
    """Create a new Git worktree with a new branch.

    Args:
        repo_path: Path to the main repository
        branch_name: Name for the new branch
        worktree_path: Path for the worktree (auto-generated if None)
        base_branch: Branch to base the new branch on (default: current branch)

    Returns:
        Tuple of (success, message_or_path)
    """
    if worktree_path is None:
        git_root = get_git_root(repo_path)
        if not git_root:
            return False, "Not a Git repository"
        worktree_path = get_worktree_path(git_root, branch_name)

    # Create parent directory for worktrees
    worktrees_dir = Path(worktree_path).parent
    try:
        worktrees_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return False, f"Failed to create worktrees directory: {e}"

    # Build command
    cmd = ["git", "worktree", "add", "-b", branch_name, worktree_path]
    if base_branch:
        cmd.append(base_branch)

    try:
        result = subprocess.run(
            cmd,
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode == 0:
            logger.info(f"Created worktree at {worktree_path} for branch {branch_name}")
            return True, worktree_path
        else:
            error = result.stderr.strip() or result.stdout.strip()
            logger.error(f"Failed to create worktree: {error}")
            return False, error

    except subprocess.TimeoutExpired:
        return False, "Operation timed out"
    except (subprocess.SubprocessError, FileNotFoundError, OSError) as e:
        return False, str(e)


def remove_worktree(worktree_path: str, force: bool = False) -> Tuple[bool, str]:
    """Remove a Git worktree.

    Args:
        worktree_path: Path to the worktree
        force: Force removal even if there are uncommitted changes

    Returns:
        Tuple of (success, message)
    """
    expanded_path = os.path.expanduser(worktree_path)

    if not os.path.exists(expanded_path):
        return True, "Worktree path does not exist"

    # Find the main repo by going up to find .git
    # Note: worktree itself has .git file, not directory
    git_root = get_git_root(expanded_path)
    if not git_root:
        # Not a git worktree, just a directory - don't remove
        return False, "Not a Git worktree"

    cmd = ["git", "worktree", "remove", expanded_path]
    if force:
        cmd.insert(3, "--force")

    try:
        result = subprocess.run(
            cmd,
            cwd=git_root,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode == 0:
            logger.info(f"Removed worktree at {worktree_path}")
            return True, "Worktree removed successfully"
        else:
            error = result.stderr.strip() or result.stdout.strip()
            logger.error(f"Failed to remove worktree: {error}")
            return False, error

    except subprocess.TimeoutExpired:
        return False, "Operation timed out"
    except (subprocess.SubprocessError, FileNotFoundError, OSError) as e:
        return False, str(e)


def delete_branch(repo_path: str, branch_name: str, force: bool = False) -> Tuple[bool, str]:
    """Delete a Git branch.

    Args:
        repo_path: Path to the repository
        branch_name: Name of the branch to delete
        force: Force deletion even if not merged

    Returns:
        Tuple of (success, message)
    """
    flag = "-D" if force else "-d"
    cmd = ["git", "branch", flag, branch_name]

    try:
        result = subprocess.run(
            cmd,
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=10,
        )

        if result.returncode == 0:
            logger.info(f"Deleted branch {branch_name}")
            return True, "Branch deleted"
        else:
            error = result.stderr.strip() or result.stdout.strip()
            return False, error

    except (subprocess.SubprocessError, FileNotFoundError, OSError) as e:
        return False, str(e)


def get_git_info(path: str) -> dict:
    """Get Git information for a directory.

    Args:
        path: Directory path to check

    Returns:
        Dictionary with Git info:
        - is_git_repo: bool
        - root: str (repository root) or None
        - current_branch: str or None
        - worktrees: list of worktree info
    """
    expanded_path = os.path.expanduser(path)

    result = {
        "is_git_repo": False,
        "root": None,
        "current_branch": None,
        "worktrees": [],
    }

    if not is_git_repo(expanded_path):
        return result

    result["is_git_repo"] = True
    result["root"] = get_git_root(expanded_path)
    result["current_branch"] = get_current_branch(expanded_path)

    if result["root"]:
        result["worktrees"] = list_worktrees(result["root"])

    return result
