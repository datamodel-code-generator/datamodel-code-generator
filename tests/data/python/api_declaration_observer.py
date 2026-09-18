"""Read actual API declaration frames without replacing model-engine methods."""

from __future__ import annotations

from types import FrameType

from datamodel_code_generator.parser.openapi_scope import ApiOpenAPIParser


class DeclarationObserver:
    """Retain only scalar declaration facts from the selected real operation."""

    def __init__(self) -> None:
        self.operation: dict[str, object] | None = None
        self.tags: list[list[str]] = []

    def record(self, frame: FrameType, event: str, arg: object) -> None:
        if event != "call" or not frame.f_globals.get("__name__", "").startswith("datamodel_code_generator.parser."):
            return
        if frame.f_code.co_name == "parse_tags":
            if tags := frame.f_locals["tags"]:
                self.tags.append(list(tags))
            return
        if frame.f_code.co_name != "_walk_parameter":
            return
        parser = frame.f_locals["self"]
        if not isinstance(parser, ApiOpenAPIParser) or not parser.declaration_frames:
            return
        operation = parser.declaration_frames[-1]
        if operation.phase != "operation" or operation.declaration.tokens != ("paths", "/root", "post"):
            return
        original = parser.raw_obj["paths"]["/root"]["post"]
        common = parser.raw_obj["paths"]["/root"]["parameters"]
        expected_parameters = [*original["parameters"], common[0]]
        effective = operation.effective_operation
        self.operation = {
            "original_indices": [[list(item.tokens) for item in group] for group in operation.original_parameters],
            "effective_indices": [list(item.tokens) for item in operation.effective_parameters],
            "root_use": list(operation.root_use_site.tokens),
            "actual_original": operation.raw_operation is original,
            "copied_operation": effective is not original,
            "borrowed_security": effective["security"] is parser.raw_obj["security"],
            "borrowed_parameters": len(effective["parameters"]) == len(expected_parameters)
            and all(actual is source for actual, source in zip(effective["parameters"], expected_parameters)),
        }
