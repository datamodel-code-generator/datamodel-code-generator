"""A test that asserts a check script's main: through the module, an alias or a nested scope, on the tree or elsewhere."""

import scripts.check_spelled
from scripts import check_alias as aliased
from scripts import check_nested, check_rooted, check_tree


def test_tree():
    assert check_tree.main(["--strict"]) == 0
    assert not scripts.check_spelled.main()
    assert aliased.main() == 0


def test_fixture(tmp_path):
    assert check_tree.main([]) == 0


def test_rooted():
    assert check_rooted.main(["--root", "elsewhere"]) == 0
    assert check_rooted.main(keywords=1) == 0


def test_discarded():
    check_tree.main([])


def helper():
    assert check_nested.main([]) == 0


def test_nested():
    def inner():
        assert check_nested.main([]) == 0

    return [check_nested.main([]) for _ in range(1)]
