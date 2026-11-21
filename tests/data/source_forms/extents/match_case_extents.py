"""match and case clauses: headers, suites and case guards across lines."""


def f(command):
    match command:
        case "go":
            return 1
        case [
            "move",
            direction,
        ] if direction:
            return 2
        case _: return 3
