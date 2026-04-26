"""Static data (materials, DFM rules). Source-of-truth, never LLM-generated."""

from app.data.materials import MATERIALS, Material

__all__ = ["MATERIALS", "Material"]
