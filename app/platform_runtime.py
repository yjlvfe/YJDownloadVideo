from __future__ import annotations

from .pot_runtime_patch import get_youtube_circuit, install


def install_runtime_patches() -> None:
    install()


__all__ = ["get_youtube_circuit", "install_runtime_patches"]
