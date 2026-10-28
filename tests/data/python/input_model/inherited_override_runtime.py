"""External input witnesses for Python field replacement."""

CASES = {
    "DefaultChild": [{}, {"value": 8}],
    "SameChild": [{}, {"value": 8}],
    "RequiredChild": [{}, {"value": 8}],
    "TypeChild": [{}, {"value": "ok"}, {"value": 8}],
    "OptionalChild": [{}, {"value": 8}],
    "RelaxedChild": [{"value": -1}, {"value": 0}, {"value": 1}, {"value": 5}],
    "TightenedChild": [{"value": 0}, {"value": 1}, {"value": 5}],
    "AliasChild": [{}, {"new": 8}, {"old": 8}],
    "RepeatedChild": [{"value": []}, {"value": [1, 1]}, {"value": [1, 2]}],
    "GrandChild": [{}, {"value": 8}],
    "InheritedGrandChild": [{}, {"value": 8}],
    "AliasSameChild": [{}, {"old": 8}],
    "AliasRequiredChild": [{}, {"new": 8}, {"old": 8}],
    "AliasGrandChild": [{}, {"newest": 8}, {"new": 8}],
    "AliasPairGrandChild": [{}, {"newest": 8, "newest_other": 9}, {"new": 8, "new_other": 9}],
    "MultipleChild": [{}, {"value": 8}],
    "RepeatedPairChild": [{"value": [1, 1], "retained": [2, 3]}],
    "RepeatedGrandChild": [{"value": [1, 1]}],
    "UniqueChild": [{"value": [1, 2]}],
    "EmptyChild": [{"value": []}, {"value": [1]}],
    "DifferentConstraintsChild": [
        {"value": 1},
        {"value": 5},
        {"value": 10},
        {"value": 11},
    ],
}

CASES.update(
    {
        "ChoicesChild": [{}, {"new": 8}],
        "PathChild": [{}],
    }
)

CASES["SharedAliasChild"] = [{"shared": [1, 1]}, {"shared": []}, {"shared": [1, 2]}]

CASES.update(
    {
        "CallerBooleanNormal": [{"value": 5}, {"value": 1}],
        "CallerDictNormal": [{"value": 5}, {"value": 1}],
        "CallerStringOverride": [{}, {"value": 1}, {"value": -1}],
        "CallerListOverride": [{}, {"value": 1}, {"value": -1}],
        "CallerDictOverride": [{}],
        "CallerInternalNormal": [{"value": 5}, {"value": 1}],
        "CallerInternalOverride": [{}, {"value": 1}, {"value": -1}],
    }
)

CASES["ClassVarAliasChild"] = [{"shared": [1, 1]}, {"shared": []}, {}]

CASES["RawSchemaIntersection"] = [{"value": 0}, {"value": 1}, {"value": 5}, {}]
