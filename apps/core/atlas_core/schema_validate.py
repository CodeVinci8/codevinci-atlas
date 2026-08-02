"""Минимальный валидатор JSON Schema (подмножество draft 2020-12).

Без внешних зависимостей: поддерживает ``type``, ``required``, ``enum``,
``properties``, ``additionalProperties`` (bool), ``items``, ``pattern``,
``minimum``/``maximum``. Достаточно для контрактов Atlas (run-result, handoff,
work-order, vp-spec). Используется при reconstruction и в CI-проверке схем.
"""

from __future__ import annotations

import re

_TYPE_MAP = {
    "object": dict, "array": list, "string": str, "integer": int,
    "number": (int, float), "boolean": bool, "null": type(None),
}


def _type_ok(value, types) -> bool:
    if isinstance(types, str):
        types = [types]
    for t in types:
        py = _TYPE_MAP.get(t)
        if py is None:
            continue
        if t == "integer" and isinstance(value, bool):
            continue  # bool не integer
        if t == "number" and isinstance(value, bool):
            continue
        if isinstance(value, py):
            return True
    return False


def validate(instance, schema, *, path: str = "$") -> list[str]:
    """Вернуть список ошибок (пусто — валидно)."""
    errors: list[str] = []
    if "type" in schema and not _type_ok(instance, schema["type"]):
        errors.append(f"{path}: ожидался type {schema['type']}, получен {type(instance).__name__}")
        return errors
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: {instance!r} не в enum {schema['enum']}")
    if "pattern" in schema and isinstance(instance, str):
        if not re.search(schema["pattern"], instance):
            errors.append(f"{path}: не соответствует pattern {schema['pattern']}")
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: {instance} < minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: {instance} > maximum {schema['maximum']}")
    if isinstance(instance, dict):
        for req in schema.get("required", []):
            if req not in instance:
                errors.append(f"{path}: отсутствует обязательное поле '{req}'")
        props = schema.get("properties", {})
        addl = schema.get("additionalProperties", True)
        for k, v in instance.items():
            if k in props:
                errors.extend(validate(v, props[k], path=f"{path}.{k}"))
            elif addl is False:
                errors.append(f"{path}: недопустимое поле '{k}' (additionalProperties=false)")
    if isinstance(instance, list) and "items" in schema:
        for i, item in enumerate(instance):
            errors.extend(validate(item, schema["items"], path=f"{path}[{i}]"))
    return errors


def is_valid(instance, schema) -> bool:
    return not validate(instance, schema)


__all__ = ["validate", "is_valid"]
