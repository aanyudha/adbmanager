import shutil
import sys
from pathlib import Path


def get_bundle_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent.parent


def get_app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def get_bundle_path(*parts: str) -> Path:
    return get_bundle_root().joinpath(*parts)


def get_runtime_path(*parts: str) -> Path:
    return get_app_root().joinpath(*parts)


def ensure_runtime_dir(*parts: str) -> Path:
    path = get_runtime_path(*parts)
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_runtime_file(relative_parts, seed_from_bundle=True) -> Path:
    path = get_runtime_path(*relative_parts)
    path.parent.mkdir(parents=True, exist_ok=True)

    if seed_from_bundle and not path.exists():
        source = get_bundle_path(*relative_parts)
        if source.exists():
            shutil.copy2(source, path)

    return path
