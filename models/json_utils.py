import json
import os
import shutil
import tempfile
import logging
import hashlib
from datetime import datetime
from models.utils import datetime_to_iso_uk

logger = logging.getLogger("app_logger")


def serialize_data(data):
    if isinstance(data, dict):
        return {k: serialize_data(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [serialize_data(v) for v in data]
    elif isinstance(data, datetime):
        return datetime_to_iso_uk(data)
    elif hasattr(data, "name"):
        return data.name
    return data


def deserialize_data(data):
    if isinstance(data, dict):
        return {k: deserialize_data(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [deserialize_data(v) for v in data]
    elif isinstance(data, str):
        try:
            # Try to parse datetime strings
            return datetime.fromisoformat(data)
        except ValueError:
            # Return as a string if it's not a datetime
            return data
    return data


def save_json(data, path, max_backups=5):
    """Serialize data and save it to a JSON file with backup rotation, atomic write, and checksum update."""

    # Make a shallow copy to avoid mutating in-memory data
    data_to_save = data.copy()

    # Backup the existing file before overwriting
    if path.exists():
        backup_with_rotation(path, max_backups)

    # Serialize the data
    serialized = serialize_data(data_to_save)

    # Create the directory if it doesn't exist
    path.parent.mkdir(parents=True, exist_ok=True)

    # Use atomic write to ensure file is written safely
    atomic_write_json(serialized, path)

    # Write the checksum after saving the JSON file
    write_checksum(path)


def load_json(path, use_checksum=True):
    """Load and deserialize JSON file with optional checksum verification."""

    if not path.exists():
        return False

    if use_checksum and not verify_checksum(path):
        logger.error("JSON checksum failed! File may be corrupted.")
        raise ValueError("Checksum mismatch!")

    # Proceed with loading the JSON data
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Deserialize the data (e.g., convert datetime strings back to datetime objects)
    return deserialize_data(data)


def backup_with_rotation(file_path, max_backups=5):
    """Backup booking JSON file and purge old backups.

    Args:
        file_path (str): Path to booking JSON file
        max_backups (int, optional): Max backups.  See config. Defaults to 5.
    """
    if not file_path.exists():
        return

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = file_path.with_name(f"{file_path.stem}-{timestamp}{file_path.suffix}")
    shutil.copy2(file_path, backup_path)

    # Cleanup old backups
    backups = sorted(
        file_path.parent.glob(f"{file_path.stem}-*{file_path.suffix}"),
        key=os.path.getmtime,
        reverse=True,
    )
    for old in backups[max_backups:]:
        old.unlink(missing_ok=True)


def atomic_write_json(data, target_path):
    """Atomic save to avoid half-saved and corrupt files.

    Args:
        data (list): Seralised booking data
        target_path (str): Path to save JSON dump
    """
    with tempfile.NamedTemporaryFile(
        "w", dir=target_path.parent, delete=False, encoding="utf-8"
    ) as tmp:
        json.dump(data, tmp, indent=2)
        temp_path = tmp.name

    os.replace(temp_path, target_path)


def verify_checksum(json_path):
    """Compare checksum to real file

    Args:
        json_path (str): Path to JSON file.

    Returns:
        Boolean: True if file checksum matches value stored in checksum file, else False.
    """
    if not json_path.with_suffix(".sha256").exists():
        return True

    try:
        content = json_path.read_text(encoding="utf-8")
        stored = json_path.with_suffix(".sha256").read_text(encoding="utf-8").strip()
        return hashlib.sha256(content.encode("utf-8")).hexdigest() == stored
    except (TypeError, ValueError) as e:
        logger.warning(
            "Problem creating digest for comparison against stored [%s] [%s]: %s",
            json_path,
            stored,
            e,
        )
        return False


def write_checksum(json_path):
    """Create and write a checksum to file.

    Args:
        json_path (str): Path to JSON file to checksum.
    """
    content = json_path.read_text(encoding="utf-8")
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    json_path.with_suffix(".sha256").write_text(digest, encoding="utf-8")
