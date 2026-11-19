"""#7129 libyaml, graphql except ImportError: raise: fallbacks under ImportError, ModuleNotFoundError and tuples."""

try:
    from yaml import CSafeLoader as SafeLoader
except ImportError:
    from yaml import SafeLoader

try:
    import graphql
except ImportError:
    raise

try:
    import tomllib
except (ModuleNotFoundError, AttributeError):
    import tomli as tomllib
else:
    import extra
finally:
    import cleanup

try:
    import bare
except:
    bare = None
