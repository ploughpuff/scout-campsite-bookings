"""json_utils.py - Utility function for managing JSON files"""

import hashlib
import json
import logging
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Type

from pydantic import BaseModel

from config import MAX_BACKUPS_TO_KEEP

logger = logging.getLogger("app_logger")


def save_json(data: BaseModel, path: Path) -> None:
    """Save a Pydantic model to JSON with backup, atomic write, and checksum."""

    if path.exists():
        backup_with_rotation(path, MAX_BACKUPS_TO_KEEP)

    serialized = data.model_dump(mode="json")  # Proper JSON-safe serialization

    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(serialized, path)
    write_checksum(path)


def load_json(path: Path, model: Type[BaseModel], use_checksum: bool = True) -> BaseModel | None:
    """Load and deserialize JSON file with optional checksum verification."""
    if not path.exists():
        return None

    if use_checksum and not verify_checksum(path):
        logger.error("JSON checksum failed! File may be corrupted.")
        raise ValueError("Checksum mismatch!")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    return model.model_validate(data)


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
