import sys
from pathlib import Path


def get_app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def get_runtime_path(*parts: str) -> Path:
    return get_app_root().joinpath(*parts)


def ensure_runtime_dir(*parts: str) -> Path:
    path = get_runtime_path(*parts)
    path.mkdir(parents=True, exist_ok=True)
    return path
