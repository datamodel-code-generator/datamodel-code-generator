from datamodel_code_generator.parser._passes import steps
from datamodel_code_generator.parser._pipeline import HookCall, PassSpec, late

PIPELINE = (
    PassSpec("parse_raw", late(steps.parse_raw)),
    PassSpec("declared", steps.declared),
    PassSpec("undeclared", lambda self, _: steps.undeclared(self)),
    PassSpec("after_final", late(steps.declared)),
    PassSpec("keyed", late(steps.keyed)),
    PassSpec("frozen_key", late(steps.keyed)),
    PassSpec("enters_phase", late(steps.enters_phase)),
    HookCall("process_single_module", late(steps.process_each_module)),
)
