class _DecisionWriter:
    def drop_held_null(self, field: object) -> None:
        self.dropped = field

    def omit_default(self, field: object) -> None:
        self.omitted = field

    def outcome(self) -> object:
        return self.dropped
