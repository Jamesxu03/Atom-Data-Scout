"""A stand-in for worker.llm.ask: canned answers per schema, and a record of every call."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel


@dataclass
class Call:
    prompt: str
    schema: type[BaseModel]
    model: str
    use_search: bool


@dataclass
class FakeAsk:
    # schema class -> a model instance, or a function(prompt) -> model instance
    responses: dict[type[BaseModel], BaseModel | Callable[[str], BaseModel]] = field(
        default_factory=dict
    )
    calls: list[Call] = field(default_factory=list)

    def __call__(self, prompt: str, schema, model: str = "", use_search: bool = False, **_: Any):
        self.calls.append(Call(prompt, schema, model, use_search))
        if schema not in self.responses:
            raise AssertionError(f"FakeAsk has no response for {schema.__name__}")
        r = self.responses[schema]
        return r(prompt) if callable(r) and not isinstance(r, BaseModel) else r

    def calls_for(self, schema) -> list[Call]:
        return [c for c in self.calls if c.schema is schema]
