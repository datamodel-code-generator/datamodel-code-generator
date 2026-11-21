"""Redundant-local-import: imports in functions, methods, class bodies and nested functions."""

import json


class Codec:
    import pickle

    def dump(self):
        import json

        return json, self.pickle


def outer():
    def inner():
        from os import path

        return path

    return inner
