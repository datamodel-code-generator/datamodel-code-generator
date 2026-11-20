"""The helper the tables are passed to: its hooks serve and list the names of a table."""


def module_hooks(module_name, table):
    def module_getattr(name):
        return table[name]

    def module_dir():
        return sorted(table)

    return module_getattr, module_dir
