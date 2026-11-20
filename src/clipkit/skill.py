from __future__ import annotations

import shutil
from pathlib import Path


def bundled_skill() -> Path | None:
    """Locate SKILL.md in an installed wheel or a source checkout."""
    packaged = Path(__file__).parent / "SKILL.md"
    if packaged.exists():
        return packaged
    # Editable/dev checkout: src/clipkit/skill.py -> repo root
    checkout = Path(__file__).resolve().parents[2] / "SKILL.md"
    if checkout.exists():
        return checkout
    return None


def install_skill(destination: str | Path) -> Path:
    source = bundled_skill()
    if source is None:
        raise FileNotFoundError("SKILL.md is not bundled with this installation")
    target_dir = Path(destination).expanduser()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "SKILL.md"
    shutil.copy2(source, target)
    return target
