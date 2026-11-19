"""Field groups: fields are grouped by their parent object, so an invoice header plus line items is two groups."""

from __future__ import annotations

from dataclasses import dataclass

from formwork_core.models import FieldSpec, FieldType, TargetSchema

HEADER = "header"


@dataclass(frozen=True)
class FieldGroup:
    name: str  # "header" or the array root path ("line_items")
    fields: tuple[FieldSpec, ...]  # leaf specs
    array_path: str | None = None

    @property
    def is_array(self) -> bool:
        return self.array_path is not None

    def key(self, spec: FieldSpec) -> str:
        """Key used for this field in tool input/output."""
        if self.array_path:
            return spec.path.split("[].", 1)[-1].replace(".", "__")
        return spec.path.replace(".", "__")

    def by_key(self) -> dict[str, FieldSpec]:
        return {self.key(f): f for f in self.fields}


def _leaves(spec: FieldSpec) -> list[FieldSpec]:
    if not spec.children:
        return [spec]
    out: list[FieldSpec] = []
    for c in spec.children:
        out.extend(_leaves(c))
    return out


def field_groups(schema: TargetSchema) -> list[FieldGroup]:
    header: list[FieldSpec] = []
    groups: list[FieldGroup] = []
    for f in schema.fields:
        if f.type is FieldType.ARRAY:
            groups.append(FieldGroup(name=f.path, fields=tuple(_leaves(f)), array_path=f.path))
        else:
            header.extend(_leaves(f))
    if header:
        groups.insert(0, FieldGroup(name=HEADER, fields=tuple(header)))
    return groups


def group_named(schema: TargetSchema, name: str) -> FieldGroup:
    for g in field_groups(schema):
        if g.name == name:
            return g
    raise KeyError(name)
