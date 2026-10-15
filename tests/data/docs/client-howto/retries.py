from app.client import Client
from app.client.options import Clock, RetryOptions
from fake import http_client

waits: list[float] = []
clock = Clock(monotonic=lambda: sum(waits), sleep=waits.append)

with Client(retry=RetryOptions(jitter="none"), clock=clock, http_client=http_client()) as client:
    response = client.default.with_response.get_health()
    print(response.data, response.info.attempt_count, waits)
