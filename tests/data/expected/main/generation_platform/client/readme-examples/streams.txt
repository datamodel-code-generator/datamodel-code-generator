# streams
example 1: imports ['Client'], calls ['client.status.get_status()']
example 2: imports ['AsyncClient'], calls ['client.status.get_status()']
example 3: imports ['APIConnectionError', 'APIStatusError', 'Client', 'NotFoundError'], calls ['client.status.get_status()']
