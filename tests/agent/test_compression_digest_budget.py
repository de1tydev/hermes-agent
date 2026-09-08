"""Long histories must not keep dispatching digest requests after cancellation."""

import time
from types import SimpleNamespace

import pytest

from agent import auxiliary_client as aux
from agent.context_compressor import ContextCompressor


def _compressor():
    return ContextCompressor(
        model="test", config_context_length=272000, quiet_mode=True,
    )


def _turns():
    return [{"role": "tool", "content": "history " * 20000,
             "tool_call_id": "call-1", "tool_name": "terminal"}]


def _response():
    return SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content="Completed historical work."),
    )])


def test_digest_stops_dispatch_after_host_cancel(monkeypatch):
    comp = _compressor()
    cancelled = False
    comp._compression_cancelled_check = lambda: cancelled
    calls = []

    def request(**kwargs):
        nonlocal cancelled
        calls.append(kwargs)
        cancelled = True
        return _response()

    monkeypatch.setattr(aux, "call_llm", request)
    with pytest.raises(aux.AuxiliaryExplicitCancellation):
        comp._build_chunk_digests(_turns())
    assert len(calls) == 1


def test_digest_budget_preserves_completed_segments_and_recovery(monkeypatch):
    comp = _compressor()
    now = [100.0]
    comp._compression_deadline = 110.0
    monkeypatch.setattr("agent.context_compressor.time.monotonic", lambda: now[0])
    calls = []

    def request(**kwargs):
        calls.append(kwargs)
        # First segment succeeds; the next crosses the optional-section budget.
        if len(calls) == 1:
            now[0] += 1
            return _response()
        now[0] = 109.0
        assert aux._aux_interrupt_cancel_requested()
        raise aux.AuxiliaryExplicitCancellation()

    monkeypatch.setattr(aux, "call_llm", request)
    result = comp._build_chunk_digests(_turns())
    assert len(calls) == 2
    assert 0 < calls[1]["timeout"] < calls[0]["timeout"] < 10
    assert "Completed historical work." in result
    assert "session_search" in result
    assert "budget" in result.lower()


def test_expired_digest_budget_does_not_dispatch(monkeypatch):
    comp = _compressor()
    comp._compression_deadline = time.monotonic() - 1
    def unexpected(**kwargs):
        pytest.fail("expired compression dispatched another request")
    monkeypatch.setattr(aux, "call_llm", unexpected)
    assert "session_search" in comp._build_chunk_digests(_turns())


def test_hard_stop_is_not_swallowed_as_optional_digest_timeout(monkeypatch):
    comp = _compressor()
    comp._compression_deadline = time.monotonic() + 60
    def request(**kwargs):
        raise aux.AuxiliaryExplicitCancellation()
    monkeypatch.setattr(aux, "call_llm", request)
    with pytest.raises(aux.AuxiliaryExplicitCancellation):
        comp._build_chunk_digests(_turns())


def test_cancelled_request_does_not_wait_for_or_release_another_requests_permit(monkeypatch):
    import threading

    semaphore = threading.BoundedSemaphore(1)
    semaphore.acquire()
    monkeypatch.setattr(aux, "_acquire_sync_aux_semaphore", lambda task: semaphore)
    with aux.aux_interrupt_protection(cancel_check=lambda: True):
        with pytest.raises(aux.AuxiliaryExplicitCancellation):
            aux.call_llm(task="compression", messages=[])
    assert not semaphore.acquire(blocking=False)
    semaphore.release()


def test_main_summary_receives_only_remaining_attempt_budget(monkeypatch):
    comp = _compressor()
    comp.tail_mode = "legacy"
    comp._compression_deadline = time.monotonic() + 2
    calls = []

    def request(**kwargs):
        calls.append(kwargs)
        return _response()

    monkeypatch.setattr("agent.context_compressor.call_llm", request)
    assert comp._generate_summary([
        {"role": "user", "content": "Summarize the historical work."}, *_turns(),
    ])
    assert len(calls) == 1
    assert 0 < calls[0]["timeout"] <= 2


def test_digest_budget_cancels_real_http_stream_without_fallback(tmp_path, monkeypatch):
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from openai import OpenAI

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    requests = []
    disconnected = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            event = {"id": "test", "object": "chat.completion.chunk", "created": 0,
                     "model": "test", "choices": [{"index": 0,
                     "delta": {"content": "progress "}, "finish_reason": None}]}
            try:
                for _ in range(200):
                    self.wfile.write(("data: " + json.dumps(event) + "\n\n").encode())
                    self.wfile.flush()
                    time.sleep(0.01)
            except (BrokenPipeError, ConnectionResetError):
                disconnected.set()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = OpenAI(api_key="test", base_url=f"http://127.0.0.1:{server.server_port}/v1",
                    max_retries=0)
    monkeypatch.setattr(aux, "_get_cached_client", lambda *a, **k: (client, "test"))
    monkeypatch.setattr(aux, "_resolve_task_provider_model",
                        lambda *a, **k: ("custom", "test", "", "", ""))
    comp = _compressor()
    comp._compression_deadline = time.monotonic() + 0.6
    try:
        with aux.aux_progress_hook(lambda: None):
            result = comp._build_chunk_digests(_turns())
        assert len(requests) == 1, "budget cancellation retried or dispatched a new segment"
        assert "budget exhausted" in result
        assert "session_search" in result
        assert disconnected.wait(1), "cancelled stream stayed open"
    finally:
        client.close()
        server.shutdown()
        server.server_close()
        thread.join(1)
