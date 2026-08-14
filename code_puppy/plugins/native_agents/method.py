"""Explicit native-method declarations and the opt-in agent mixin."""

from __future__ import annotations

import ast
import functools
import inspect
import textwrap
from collections.abc import Mapping
from typing import Any, get_type_hints

from pydantic import BaseModel

from .contracts import ContextBudget, MethodSpec, NativeStrategyName
from .errors import NativeContractError, NativeRuntimeDisabledError

SPEC_ATTRIBUTE = "__native_method_spec__"


def _annotation_matches(annotation: Any, expected: type[BaseModel]) -> bool:
    if annotation is expected:
        return True
    return (
        isinstance(annotation, str)
        and annotation.rsplit(".", 1)[-1] == expected.__name__
    )


def _is_stub(function: Any) -> bool:
    """Return whether a declaration body contains only a docstring and ellipsis."""

    try:
        source = textwrap.dedent(inspect.getsource(function))
        node = ast.parse(source).body[0]
    except (OSError, IOError, SyntaxError) as exc:
        raise NativeContractError(
            "native method declarations must have an inspectable stub body"
        ) from exc
    if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
        return False
    body = list(node.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body.pop(0)
    return (
        len(body) == 1
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and body[0].value.value is Ellipsis
    )


def _validate_declaration(
    function: Any,
    *,
    input_type: type[BaseModel],
    output_type: type[BaseModel],
    state_type: type[BaseModel] | None,
    strategy: str | NativeStrategyName,
    allowed_capabilities: tuple[str, ...],
    max_validation_repairs: int,
) -> None:
    if not inspect.iscoroutinefunction(function):
        raise NativeContractError("native methods must be declared with async def")
    if not issubclass(input_type, BaseModel) or not issubclass(output_type, BaseModel):
        raise NativeContractError(
            "native input and output types must be Pydantic models"
        )
    if state_type is not None and not issubclass(state_type, BaseModel):
        raise NativeContractError("native state type must be a Pydantic model")
    if strategy != NativeStrategyName.PREDICT and strategy != "predict":
        raise NativeContractError("the codeact strategy is not available in Tier A")
    if allowed_capabilities:
        raise NativeContractError("capabilities are not available before Phase 4")
    if not 0 <= max_validation_repairs <= 3:
        raise NativeContractError("validation repairs must be between 0 and 3")
    signature = inspect.signature(function)
    parameters = list(signature.parameters.values())
    if len(parameters) != 2 or parameters[0].name != "self":
        raise NativeContractError("native methods accept self and one input model")
    if parameters[1].kind not in {
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    }:
        raise NativeContractError("native input must be one positional model")
    if not _annotation_matches(parameters[1].annotation, input_type):
        try:
            hints = get_type_hints(function)
        except (NameError, TypeError) as exc:
            raise NativeContractError(
                "native input annotation cannot be resolved"
            ) from exc
        if hints.get(parameters[1].name) is not input_type:
            raise NativeContractError("native input annotation must match input_type")
    if not _annotation_matches(signature.return_annotation, output_type):
        try:
            hints = get_type_hints(function)
        except (NameError, TypeError) as exc:
            raise NativeContractError(
                "native return annotation cannot be resolved"
            ) from exc
        if hints.get("return") is not output_type:
            raise NativeContractError("native return annotation must match output_type")
    if not _is_stub(function):
        raise NativeContractError(
            "native method bodies must contain only a docstring and ..."
        )


def native_method(
    *,
    strategy: str | NativeStrategyName,
    input_type: type[BaseModel],
    output_type: type[BaseModel],
    state_type: type[BaseModel] | None = None,
    version: int = 1,
    max_validation_repairs: int = 1,
    context_budget: ContextBudget | None = None,
    name: str | None = None,
    capabilities: tuple[str, ...] = (),
):
    """Decorate one explicit async typed native method declaration."""

    try:
        strategy_name = NativeStrategyName(strategy)
    except ValueError as exc:
        raise NativeContractError(f"unknown native strategy {strategy!r}") from exc
    if strategy_name is not NativeStrategyName.PREDICT:
        raise NativeContractError("the codeact strategy is not available in Tier A")
    budget = context_budget or ContextBudget(
        max_chars=12_000,
        max_events=30,
        max_preview_items=0,
    )

    def decorate(function: Any) -> Any:
        _validate_declaration(
            function,
            input_type=input_type,
            output_type=output_type,
            state_type=state_type,
            strategy=strategy_name,
            allowed_capabilities=capabilities,
            max_validation_repairs=max_validation_repairs,
        )
        method_name = name or function.__name__
        if not method_name or method_name.startswith("_"):
            raise NativeContractError(
                "native method names must be public and non-empty"
            )
        spec = MethodSpec(
            name=method_name,
            version=version,
            strategy=strategy_name,
            input_schema_name=input_type.__name__,
            output_schema_name=output_type.__name__,
            state_schema_name=state_type.__name__ if state_type else None,
            allowed_capabilities=capabilities,
            input_type=input_type,
            output_type=output_type,
            state_type=state_type,
            max_validation_repairs=max_validation_repairs,
            context_budget=budget,
            instructions=inspect.getdoc(function) or "",
        )

        @functools.wraps(function)
        async def invoke(self: Any, request: BaseModel) -> BaseModel:
            if not isinstance(request, input_type):
                raise NativeContractError(
                    f"native method {method_name!r} received the wrong input model"
                )
            runtime = getattr(self, "_native_method_runtime", None)
            if runtime is None:
                raise NativeRuntimeDisabledError(
                    "native method runtime is not configured for this agent"
                )
            return await runtime.execute(self, spec, request)

        setattr(invoke, SPEC_ATTRIBUTE, spec)
        return invoke

    return decorate


class NativeAgentMixin:
    """Opt-in registry for classes that explicitly declare native methods."""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        registry: dict[str, MethodSpec] = {}
        identifiers: dict[str, str] = {}
        for candidate_name in reversed(inspect.getmro(cls)):
            for attribute_name, member in candidate_name.__dict__.items():
                spec = getattr(member, SPEC_ATTRIBUTE, None)
                if spec is None:
                    continue
                previous = identifiers.get(spec.name)
                if previous is not None and previous != attribute_name:
                    raise NativeContractError(
                        f"duplicate native method identifier {spec.name!r}"
                    )
                identifiers[spec.name] = attribute_name
                registry[attribute_name] = spec
        cls.__native_method_registry__ = registry

    @property
    def _native_method_runtime(self):
        runtime = getattr(self, "_native_runtime_instance", None)
        if runtime is None:
            from .runtime import NativeMethodRuntime

            runtime = NativeMethodRuntime()
            setattr(self, "_native_runtime_instance", runtime)
        return runtime

    def native_methods(self) -> Mapping[str, MethodSpec]:
        return dict(getattr(type(self), "__native_method_registry__", {}))

    def get_native_method(self, name: str) -> MethodSpec:
        methods = self.native_methods()
        if name in methods:
            return methods[name]
        for spec in methods.values():
            if spec.name == name:
                return spec
        raise NativeContractError(f"unknown native method {name!r}")
