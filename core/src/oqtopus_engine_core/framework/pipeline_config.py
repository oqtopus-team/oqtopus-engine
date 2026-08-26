"""Pydantic schema for the `pipeline_manager` config section.

This is the structural validation layer (Layer 1): it checks the shape of
the config dict itself and has no dependency on the DI container or the
condition DSL. DSL syntax/field/type checks and DI name resolution happen
afterwards in `PipelineBuilder` (Layer 2).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PipelineDefinition(BaseModel):
    """A single pipeline entry under `pipeline_manager.pipelines`."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    name: str
    if_: str = Field(alias="if")
    steps: list[str]

    @field_validator("if_", mode="before")
    @classmethod
    def _coerce_bool_literal(cls, v: object) -> object:
        # YAML parses an unquoted `if: true`/`if: false` as a native bool,
        # not the string "true"/"false" the condition DSL expects. Coerce
        # it here so config authors don't need to quote the literal.
        if isinstance(v, bool):
            return "true" if v else "false"
        return v

    @field_validator("steps")
    @classmethod
    def _steps_not_empty(cls, v: list[str]) -> list[str]:
        if not v:
            message = "steps must not be empty"
            raise ValueError(message)
        return v


class PipelineManagerConfig(BaseModel):
    """Top-level schema for the `pipeline_manager` config section."""

    model_config = ConfigDict(extra="forbid")

    job_buffer: str
    exception_handler: str
    pipelines: list[PipelineDefinition]

    @field_validator("pipelines")
    @classmethod
    def _validate_pipelines(
        cls, pipelines: list[PipelineDefinition]
    ) -> list[PipelineDefinition]:
        if not pipelines:
            message = "pipelines must contain at least one entry"
            raise ValueError(message)

        names = [p.name for p in pipelines]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            message = f"duplicate pipeline name(s): {dupes}"
            raise ValueError(message)

        return pipelines
