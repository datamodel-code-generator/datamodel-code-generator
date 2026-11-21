"""Loop and with targets bind in the enclosing scope, async forms included."""


async def read(paths, opener):
    for index, path in enumerate(paths):
        with opener(path) as handle, opener(path) as (head, tail):
            pass
    async for item in opener(paths):
        async with opener(item) as stream:
            pass
    return index, handle, head, tail, stream
