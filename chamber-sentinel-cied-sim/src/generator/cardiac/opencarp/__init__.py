"""openCARP template generation and runtime library.

Public API:
    TemplateLibrary  -- Runtime loader for pre-computed EGM beat templates.
    IonicAdapter     -- Adapts template output to the EGM synthesizer interface.
    TemplateGenerator -- Offline generator (requires openCARP or uses synthetic fallback).
"""

from .template_library import TemplateLibrary
from .ionic_adapter import IonicAdapter
from .template_generator import TemplateGenerator

__all__ = [
    "TemplateLibrary",
    "IonicAdapter",
    "TemplateGenerator",
]
