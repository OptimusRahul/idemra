"""LLM router config + injectable completion — no real network/API calls
anywhere in this suite. build_router() is exercised for real (it's just
config construction, no network); complete() always takes a fake router.
"""

from idemra.llm.router import DEFAULT_MODEL_ALIAS, build_router, complete


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


class _FakeRouter:
    def __init__(self, content: str) -> None:
        self._content = content
        self.calls: list[dict] = []

    def completion(self, model: str, messages: list[dict]) -> _FakeResponse:
        self.calls.append({"model": model, "messages": messages})
        return _FakeResponse(self._content)


def test_build_router_has_local_and_cloud_entries_under_one_alias() -> None:
    router = build_router()

    aliases = {entry["model_name"] for entry in router.model_list}
    assert aliases == {DEFAULT_MODEL_ALIAS}
    assert len(router.model_list) == 2


def test_complete_calls_router_with_prompt_and_returns_content() -> None:
    fake = _FakeRouter("proposed content")

    result = complete("write me a function", router=fake)

    assert result == "proposed content"
    assert fake.calls[0]["model"] == DEFAULT_MODEL_ALIAS
    assert fake.calls[0]["messages"] == [{"role": "user", "content": "write me a function"}]
