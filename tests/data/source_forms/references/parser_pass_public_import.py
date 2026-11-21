"""#7025 architecture boundaries: a pass module may import ``generation`` from the parser package, and no other module.

layers: parser-passes
"""
from datamodel_code_generator.parser import generation

__all__ = ["generation"]
