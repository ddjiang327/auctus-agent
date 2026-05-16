"""Evolution module for closed learning loop.

This module provides:
- skills: Automatic skill extraction and management
- eval: Evaluation set generation and automated testing
- gepa: GEPA (Generate-Evaluate-Prune-Apply) optimization pipeline
- traces: Trace collection and storage for eval/skill generation

Usage:
    from app.evolve import skills, eval, gepa, traces
"""

from . import skills
from . import eval
from . import gepa
from . import traces

__all__ = ["skills", "eval", "gepa", "traces"]
