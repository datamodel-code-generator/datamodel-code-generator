"""Review R26, N4: parenthesized with items bind; a binding in a for loop's else has the loop's else guard."""


def f(a, b):
    with (open(a) as fa, open(b) as fb):
        for line in fa:
            break
        else:
            line = None
    while fa:
        break
    else:
        done = True
    return line, fb, done
