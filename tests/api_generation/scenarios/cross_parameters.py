"""Call the parameters server through its client: cookies, deepObject, unexploded and header arrays, content, enums.

The document also holds repeated path placeholders, dotted and non-ASCII names, root model and literal defaults.
A refused path parameter is reported under the name the document gives it, however the route path spells it. Path
parameters that leave out `required` or declare it false are required arguments of both the client and the server.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.api_generation.support.cross_targets import service

if TYPE_CHECKING:
    from types import ModuleType

    from tests.api_generation.support.cross_targets import Calls, Kit, Recorder

DOCUMENT = "fastapi/server-parameters.yaml"
CLIENT = {"default_base_url": "https://parameters.example.com"}


def services(server: ModuleType, models: ModuleType, record: Recorder) -> dict[str, object]:
    """Return a service whose every method records its typed arguments."""
    del models
    return {"default": service(server.services.DefaultService, record)}


def calls(kit: Kit) -> Calls:
    """Return calls that give each parameter a value, its default, or a value to encode."""
    api, models = kit.api, kit.models
    level = models.ItemsLevelGetPathLevelParameter.integer_2
    mode = models.ItemsLevelGetQueryModeParameter.integer_1
    ratio = models.ItemsLevelGetQueryRatioParameter.number_1_5
    kind = models.ItemsLevelGetQueryKindParameter.b
    levels = [models.ItemsLevelGetQueryLevelsParameterEnum.integer_2] * 2
    return [
        (
            "search every parameter",
            lambda: api.default.search(
                session="ab",
                code="XYZ",
                filter=models.Filter(id=1, name="a b"),
                ids=[1, 2, 3],
                X_Ids=[4, 5],
                blob=models.Blob(a=1),
                note="hello, world",
            ),
        ),
        ("search defaults", lambda: api.default.search(session="ab")),
        ("search refused cookie", lambda: api.default.search(session="a")),
        ("get_ratios", lambda: api.default.get_ratios(ratios=[0.5, 1.25])),
        ("get_ratios limits", lambda: api.default.get_ratios(ratios=[2.0], limits=[3])),
        ("get_sizes defaults", api.default.get_sizes),
        ("get_sizes values", lambda: api.default.get_sizes(size=3, words=["x", "y z"], one="x", least=8, cap=4)),
        ("get_sizes null", lambda: api.default.get_sizes(cap=None)),
        ("get_codes", lambda: api.default.get_codes(X_Codes=[1, 2])),
        ("repeat", lambda: api.default.repeat(id=5)),
        ("get_note", lambda: api.default.get_note(note_id=3)),
        ("get_file", lambda: api.default.get_file(file_name="a.txt")),
        ("get_file refused name", lambda: api.default.get_file(file_name="A.txt")),
        ("get_pair", lambda: api.default.get_pair(pair_id="ab", class_=2, part=3, X_Rank=4)),
        ("get_pair refused names", lambda: api.default.get_pair(pair_id="AB", class_=0, part=3, X_Rank=0)),
        ("get_pair refused part", lambda: api.default.get_pair(pair_id="ab", class_=2, part=0)),
        ("get_menu", lambda: api.default.get_menu(item="abc")),
        (
            "get_item every parameter",
            lambda: api.default.get_item(level=level, mode=mode, ratio=ratio, X_Flag=True, kind=kind, levels=levels),
        ),
        ("get_item level", lambda: api.default.get_item(level=level)),
        ("get_serving", lambda: api.default.get_serving(food_id="apple", serving=2)),
        ("get_serving refused serving", lambda: api.default.get_serving(food_id="apple", serving=0)),
    ]
