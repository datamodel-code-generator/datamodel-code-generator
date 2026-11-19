"""coverage_exclusions: the region a coverage mark excludes is the marked statement, or a clause header and its suite.

A definition's header starts at its first decorator; ``else`` and ``finally`` have no node of their own and are found
by their keyword; a one-line clause has no body apart from its header, and a nested region stays in its owner's suite.
"""


def decorate(function):
    return function


@decorate
def decorated(  # pragma: no cover
    value,
):
    return value


def clauses(items, subject):
    for item in items:
        pass
    else:  # pragma: no cover
        items = None
    try:
        pass
    except ValueError:  # pragma: no cover
        items = []
    finally:  # pragma: no cover
        subject = None
    match subject:
        case None:  # pragma: no cover
            subject = 0
    if subject: subject = 1  # pragma: no cover
    with open(items) as handle:  # pragma: no cover
        if handle:  # pragma: no cover
            handle = None
    return items  # pragma: no cover


class Holder:  # pragma: no cover
    @decorate
    def method(self):
        return self


def continued(flag):
    if flag: flag = print(
        flag,
    )  # pragma: no cover
