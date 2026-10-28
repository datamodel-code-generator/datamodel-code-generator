"""Validated model inputs whose custom serializers disagree with declared fields."""
from pydantic import PrivateAttr, model_serializer, model_validator


def model_inputs(root, item):
    class RewriteAllowed(item):
        @model_serializer
        def rewrite(self):
            return {'a-value': 1, 'b': ['x']}

    class RewriteInvalid(item):
        @model_serializer
        def rewrite(self):
            return {'a-value': 9, 'b': ['y']}

    class RewriteRoot(root):
        @model_serializer
        def rewrite(self):
            return [{'a-value': 1, 'b': ['x']}]

    nested = item.model_fields['b'].annotation

    class RewriteNestedAllowed(nested):
        @model_serializer
        def rewrite(self):
            return ['x']

    class RewriteNestedInvalid(nested):
        @model_serializer
        def rewrite(self):
            return ['y']

    class CountValidation(item):
        _calls: int = PrivateAttr(default=0)

        @model_validator(mode='after')
        def count(self):
            self._calls += 1
            return self

    class MutateValidation(item):
        @model_validator(mode='after')
        def mutate(self):
            self.a_value += 1
            return self

    class CountRoot(root):
        root: list[CountValidation]

    class MutateRoot(root):
        root: list[MutateValidation]

    valid = {'a-value': 1, 'b': ['x']}
    invalid = {'a-value': 9, 'b': ['x']}
    return [
        ('standard-valid', root, [item.model_validate(valid)]),
        ('standard-invalid', root, [item.model_validate(invalid)]),
        ('enum-invalid', root, [item.model_validate({'a-value': 1, 'b': ['y']})]),
        ('serializer-valid', root, [RewriteInvalid.model_validate(valid)]),
        ('serializer-invalid', root, [RewriteAllowed.model_validate(invalid)]),
        ('root-serializer-valid', RewriteRoot, [item.model_validate(valid)]),
        ('root-serializer-invalid', RewriteRoot, [item.model_validate(invalid)]),
        ('mixed-valid', root, [{'a-value': 1, 'b': nested(['x'])}]),
        ('mixed-invalid', root, [{'a-value': 9, 'b': nested(['x'])}]),
        ('mixed-serializer-valid', root, [{'a-value': 1, 'b': RewriteNestedInvalid(['x'])}]),
        ('mixed-serializer-invalid', root, [{'a-value': 1, 'b': RewriteNestedAllowed(['y'])}]),
        ('mixed-string-invalid', root, [{'a-value': '1', 'b': nested(['x'])}]),
        ('mixed-bool-invalid', root, [{'a-value': True, 'b': nested(['x'])}]),
        ('after-counter', CountRoot, [CountValidation.model_validate(valid)]),
        ('after-mutation', MutateRoot, [MutateValidation.model_construct(a_value=0, b=nested(['x']))]),
        ('opaque-model', root, [item.model_construct(a_value=1, b=len)]),
        ('opaque', root, [len]),
    ]
