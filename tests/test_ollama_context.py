import json
from unittest.mock import AsyncMock

from app.news.models import ExtractedNewsSource, NewsSourceKind
from app.news.ollama_client import (
    FACT_SYSTEM_PROMPT,
    NEWS_SYSTEM_PROMPT,
    REDUCE_SYSTEM_PROMPT,
    OllamaNewsClient,
)
from app.news.worker_models import FactBundle, NewsDraft


def _source(chunk_count: int, chunk_size: int = 90) -> ExtractedNewsSource:
    raw_text = "\n".join(
        f"fragment-{index}:" + chr(65 + index % 26) * chunk_size
        for index in range(chunk_count)
    )
    return ExtractedNewsSource(
        kind=NewsSourceKind.MANUAL,
        source_id="context-test",
        source_url=None,
        normalized_url=None,
        title="Context test",
        raw_text=raw_text,
    )


def _bulky_bundle(marker: str) -> FactBundle:
    return FactBundle(
        summary=((marker + " summary ") * 100)[:1200],
        facts=[f"{marker} fact {index} " + "f" * 280 for index in range(6)],
        names=[f"{marker} name {index} " + "n" * 70 for index in range(3)],
        dates=[f"{marker} date {index} " + "d" * 35 for index in range(3)],
        numbers=[f"{marker} number {index} " + "1" * 30 for index in range(3)],
        warnings=[f"{marker} warning " + "w" * 80],
    )


def _draft() -> NewsDraft:
    return NewsDraft(headline="Итог", body="Итоговая новость")


async def test_8192_context_uses_hierarchical_reduce_before_final_call():
    client = OllamaNewsClient(
        AsyncMock(),
        base_url="http://127.0.0.1:11434",
        context_length=8192,
        max_predict_tokens=1600,
        max_source_chars_per_chunk=110,
        max_source_chunks=16,
    )
    fact_index = 0

    async def structured(system_prompt, user_prompt, response_model):
        nonlocal fact_index
        if system_prompt == FACT_SYSTEM_PROMPT:
            fact_index += 1
            return _bulky_bundle(f"chunk-{fact_index}")
        if system_prompt == REDUCE_SYSTEM_PROMPT:
            payload = json.loads(user_prompt)
            return _bulky_bundle(f"level-{payload['reduction_level']}")
        assert system_prompt == NEWS_SYSTEM_PROMPT
        assert response_model is NewsDraft
        return _draft()

    client._structured_chat = AsyncMock(side_effect=structured)

    result = await client.rewrite(_source(8))

    assert result.headline == "Итог"
    reduction_payloads = [
        json.loads(call.args[1])
        for call in client._structured_chat.await_args_list
        if call.args[0] == REDUCE_SYSTEM_PROMPT
    ]
    assert {payload["reduction_level"] for payload in reduction_payloads} == {1, 2}
    final_call = client._structured_chat.await_args_list[-1]
    final_prompt = final_call.args[1]
    budget = client._user_prompt_char_budget(NEWS_SYSTEM_PROMPT, NewsDraft)
    assert len(final_prompt) <= budget
    assert len(json.loads(final_prompt)["evidence_from_all_chunks"]) < 8
    for call in client._structured_chat.await_args_list:
        system_prompt, user_prompt, response_model = call.args
        assert len(user_prompt) <= client._user_prompt_char_budget(
            system_prompt,
            response_model,
        )


async def test_large_context_keeps_all_fact_bundles_without_unneeded_reduce():
    client = OllamaNewsClient(
        AsyncMock(),
        base_url="http://127.0.0.1:11434",
        context_length=65536,
        max_predict_tokens=1600,
        max_source_chars_per_chunk=110,
        max_source_chunks=16,
    )
    fact_index = 0

    async def structured(system_prompt, user_prompt, response_model):
        nonlocal fact_index
        if system_prompt == FACT_SYSTEM_PROMPT:
            fact_index += 1
            return _bulky_bundle(f"chunk-{fact_index}")
        if system_prompt == REDUCE_SYSTEM_PROMPT:
            raise AssertionError("large context must not reduce evidence that already fits")
        return _draft()

    client._structured_chat = AsyncMock(side_effect=structured)

    await client.rewrite(_source(8))

    final_prompt = client._structured_chat.await_args_list[-1].args[1]
    assert len(json.loads(final_prompt)["evidence_from_all_chunks"]) == 8
    assert all(
        call.args[0] != REDUCE_SYSTEM_PROMPT
        for call in client._structured_chat.await_args_list
    )


async def test_oversized_single_source_never_reaches_final_call_directly():
    client = OllamaNewsClient(
        AsyncMock(),
        base_url="http://127.0.0.1:11434",
        context_length=8192,
        max_predict_tokens=1600,
        max_source_chars_per_chunk=20000,
        max_source_chunks=2,
    )

    async def structured(system_prompt, user_prompt, response_model):
        if system_prompt == FACT_SYSTEM_PROMPT:
            return _bulky_bundle("single")
        return _draft()

    client._structured_chat = AsyncMock(side_effect=structured)
    source = _source(1, chunk_size=11000)

    await client.rewrite(source)

    calls = client._structured_chat.await_args_list
    assert calls[0].args[0] == FACT_SYSTEM_PROMPT
    assert calls[-1].args[0] == NEWS_SYSTEM_PROMPT
    final_prompt = calls[-1].args[1]
    assert "source_text_untrusted" not in json.loads(final_prompt)
    assert len(final_prompt) <= client._user_prompt_char_budget(NEWS_SYSTEM_PROMPT, NewsDraft)


async def test_fact_chunks_honor_real_json_budget_with_default_chunk_size():
    client = OllamaNewsClient(
        AsyncMock(),
        base_url="http://127.0.0.1:11434",
        context_length=8192,
        max_predict_tokens=1600,
        max_source_chars_per_chunk=24000,
        max_source_chunks=16,
    )

    async def structured(system_prompt, user_prompt, response_model):
        if system_prompt == FACT_SYSTEM_PROMPT:
            return FactBundle(summary="Краткий набор", facts=["Проверенный факт"])
        return _draft()

    client._structured_chat = AsyncMock(side_effect=structured)
    source = _source(1, chunk_size=16000)
    source.raw_text = '"\\' * 8000

    await client.rewrite(source)

    fact_calls = [
        call
        for call in client._structured_chat.await_args_list
        if call.args[0] == FACT_SYSTEM_PROMPT
    ]
    assert len(fact_calls) > 1
    fact_budget = client._user_prompt_char_budget(FACT_SYSTEM_PROMPT, FactBundle)
    assert all(len(call.args[1]) <= fact_budget for call in fact_calls)
