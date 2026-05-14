"""Evolution module for closed learning loop.

This module provides:
- skills: Automatic skill extraction and management
- eval: Evaluation set generation and automated testing
- gepa: GEPA (Generate-Evaluate-Prune-Apply) optimization pipeline

Usage:
    from app.evolve import skills, eval, gepa
"""

from . import skills
from . import eval
from . import gepa

__all__ = ["skills", "eval", "gepa"]
