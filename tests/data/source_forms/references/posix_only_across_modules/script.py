"""A script whose worker forks, with an entry that dispatches and a function that stays portable."""

import os


def worker():
    return os.fork()


def run(argv):
    return worker() if argv else 0


def main(argv):
    return run(argv)


def portable(argv):
    return len(argv)


class Runner:
    def start(self):
        return worker()
