import json

import httpx

from tests.fakes import ask_body, make_client, question_of


def test_ask_records_response_and_latency():
    def handler(request):
        assert request.url.path == "/ask" and question_of(request) == "hello there"
        return httpx.Response(200, json=ask_body())

    client, _ = make_client(handler)
    ex = client.ask("hello there")
    assert ex.status == 200 and ex.attempts == 1
    assert ex.answer == "The limit is 65 dB [1]." and ex.refused is False
    assert len(ex.citations) == 1 and ex.latency_ms == 0.0
    assert json.loads(ex.request_body) == {"question": "hello there"}


def test_retries_429_then_succeeds_and_honours_retry_after():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(429, json={"detail": "slow down"}, headers={"retry-after": "7"})
        return httpx.Response(200, json=ask_body())

    client, sleeps = make_client(handler, rpm=60)
    ex = client.ask("hello there")
    assert ex.status == 200 and ex.attempts == 3
    assert sleeps.count(7.0) == 2


def test_gives_up_and_records_the_last_status():
    client, _ = make_client(lambda r: httpx.Response(503, json={"detail": "busy"}), max_retries=2)
    ex = client.ask("hello there")
    assert ex.status == 503 and ex.attempts == 3 and ex.json == {"detail": "busy"}


def test_exponential_backoff_without_retry_after():
    client, sleeps = make_client(lambda r: httpx.Response(503), max_retries=2, backoff=5, rpm=6000)
    client.ask("hello there")
    assert [s for s in sleeps if s >= 5] == [5.0, 10.0]


def test_does_not_retry_client_errors():
    client, _ = make_client(lambda r: httpx.Response(422, json={"detail": []}))
    ex = client.ask("hello there")
    assert ex.status == 422 and ex.attempts == 1


def test_transport_error_is_recorded_not_raised():
    def handler(request):
        raise httpx.ConnectError("refused")

    client, _ = make_client(handler, max_retries=1)
    ex = client.ask("hello there")
    assert ex.status is None and ex.attempts == 2 and "ConnectError" in ex.error


def test_transport_error_clears_stale_body_from_earlier_attempt():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(503, json={"detail": "busy"})
        raise httpx.ConnectError("gone")

    client, _ = make_client(handler, max_retries=1)
    ex = client.ask("hello there")
    assert ex.status is None and ex.text == "" and ex.body is None


def test_requests_are_paced_to_the_rate_limit():
    client, sleeps = make_client(lambda r: httpx.Response(200, json=ask_body()), rpm=30)
    for _ in range(3):
        client.ask("hello there")
    assert sleeps == [2.0, 2.0]  # 60/30 s between requests, none before the first


def test_raw_body_is_sent_verbatim():
    seen = {}

    def handler(request):
        seen["body"] = request.content
        return httpx.Response(422, json={"detail": []})

    client, _ = make_client(handler)
    client.send("POST", "/ask", "{not json")
    assert seen["body"] == b"{not json"


def test_non_json_body_is_kept_as_text():
    client, _ = make_client(lambda r: httpx.Response(500, text="Internal Server Error"))
    ex = client.ask("hello there")
    assert ex.body is None and ex.text == "Internal Server Error" and ex.answer is None


def test_health():
    client, _ = make_client(lambda r: httpx.Response(200, json={"status": "ok"}))
    assert client.health() == {"status": "ok"}
    client, _ = make_client(lambda r: httpx.Response(500), max_retries=0)
    assert client.health() is None
