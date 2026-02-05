"""Random memorable session name generator."""

import random

ADJECTIVES = [
    "happy", "swift", "calm", "brave", "clever", "gentle",
    "fierce", "quiet", "bold", "wise", "bright", "cool",
    "eager", "fancy", "grand", "jolly", "keen", "lucky",
    "merry", "noble", "proud", "quick", "royal", "sharp",
    "agile", "cosmic", "daring", "epic", "fresh", "golden",
    "humble", "iron", "jade", "kind", "lively", "magic",
]

ANIMALS = [
    "panda", "falcon", "turtle", "tiger", "eagle", "dolphin",
    "fox", "wolf", "bear", "hawk", "lion", "owl",
    "raven", "shark", "whale", "cobra", "crane", "deer",
    "otter", "phoenix", "dragon", "koala", "lynx", "seal",
    "badger", "condor", "ferret", "gopher", "hedgehog", "impala",
    "jackal", "lemur", "moose", "narwhal", "osprey", "panther",
]


def generate_session_name() -> str:
    """Generate a random memorable session name.

    Returns:
        A name in the format: {adjective}-{animal}-{number}
        Example: "happy-panda-42"
    """
    adj = random.choice(ADJECTIVES)
    animal = random.choice(ANIMALS)
    num = random.randint(1, 99)
    return f"{adj}-{animal}-{num}"


def generate_unique_name(existing_names: list[str], max_attempts: int = 10) -> str:
    """Generate a unique session name not in existing_names.

    Args:
        existing_names: List of names to avoid
        max_attempts: Maximum generation attempts

    Returns:
        A unique memorable name
    """
    for _ in range(max_attempts):
        name = generate_session_name()
        if name not in existing_names:
            return name

    # Fallback: add extra random suffix
    base = generate_session_name()
    suffix = random.randint(100, 999)
    return f"{base}-{suffix}"
