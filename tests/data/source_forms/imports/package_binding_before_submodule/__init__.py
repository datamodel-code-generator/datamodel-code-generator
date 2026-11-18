"""from pkg import tool reads what pkg binds as tool; the submodule pkg.tool only where pkg binds no such name.

The package binds ``tool`` itself and leaves ``helper`` to its submodule.
"""


def tool():
    return "the package's tool"
