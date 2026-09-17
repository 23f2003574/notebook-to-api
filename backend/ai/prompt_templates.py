from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException

_PLACEHOLDER_PATTERN = re.compile(r"\{(\w+)\}")


class TemplateAlreadyExistsError(ValueError):
    pass


class UnknownTemplateError(KeyError):
    pass


class TemplateValidationError(ValueError):
    pass


class MissingVariableError(ValueError):
    pass


@dataclass(frozen=True)
class TemplateVariable:
    """A named placeholder a prompt template expects to be filled in."""

    name: str
    required: bool = True
    default: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "required": self.required,
            "default": self.default,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "TemplateVariable":
        return cls(
            name=payload.get("name", ""),
            required=payload.get("required", True),
            default=payload.get("default"),
        )


@dataclass(frozen=True)
class PromptTemplate:
    """A reusable, parameterized prompt with a versioned edit history."""

    name: str
    version: int
    text: str
    variables: tuple
    created_at: datetime
    updated_at: datetime

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "text": self.text,
            "variables": [variable.to_dict() for variable in self.variables],
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


class PromptTemplateManager:
    """Creates, renders, and manages the lifecycle of reusable prompt templates."""

    def __init__(self) -> None:
        self._templates: dict = {}
        self._lock = Lock()

    @staticmethod
    def _validate(text: str, variables: tuple) -> None:
        if not text:
            raise TemplateValidationError("template text is required")
        placeholders = set(_PLACEHOLDER_PATTERN.findall(text))
        declared = {variable.name for variable in variables}
        undeclared = placeholders - declared
        if undeclared:
            raise TemplateValidationError(
                f"template references undeclared variables: {', '.join(sorted(undeclared))}"
            )
        unused = declared - placeholders
        if unused:
            raise TemplateValidationError(
                f"declared variables not used in template text: {', '.join(sorted(unused))}"
            )

    def create(
        self,
        name: str,
        text: str,
        variables: Optional[list] = None,
    ) -> PromptTemplate:
        if not name:
            raise ValueError("template name is required")
        variables = tuple(variables or [])
        self._validate(text, variables)
        with self._lock:
            if name in self._templates:
                raise TemplateAlreadyExistsError(f"template '{name}' already exists")
            now = datetime.now(timezone.utc)
            template = PromptTemplate(
                name=name,
                version=1,
                text=text,
                variables=variables,
                created_at=now,
                updated_at=now,
            )
            self._templates[name] = template
        return template

    def render(self, name: str, values: Optional[dict] = None) -> str:
        template = self.get(name)
        values = values or {}
        rendered = template.text
        for variable in template.variables:
            if variable.name in values:
                value = values[variable.name]
            elif variable.default is not None:
                value = variable.default
            elif variable.required:
                raise MissingVariableError(
                    f"missing required variable '{variable.name}' for template '{name}'"
                )
            else:
                value = ""
            rendered = rendered.replace("{" + variable.name + "}", str(value))
        return rendered

    def update(
        self,
        name: str,
        text: Optional[str] = None,
        variables: Optional[list] = None,
    ) -> PromptTemplate:
        with self._lock:
            existing = self._templates.get(name)
            if existing is None:
                raise UnknownTemplateError(name)
            new_text = text if text is not None else existing.text
            new_variables = tuple(variables) if variables is not None else existing.variables

        self._validate(new_text, new_variables)

        with self._lock:
            updated = replace(
                existing,
                text=new_text,
                variables=new_variables,
                version=existing.version + 1,
                updated_at=datetime.now(timezone.utc),
            )
            self._templates[name] = updated
        return updated

    def delete(self, name: str) -> None:
        with self._lock:
            if name not in self._templates:
                raise UnknownTemplateError(name)
            del self._templates[name]

    def get(self, name: str) -> PromptTemplate:
        with self._lock:
            template = self._templates.get(name)
        if template is None:
            raise UnknownTemplateError(name)
        return template

    def list_templates(self) -> list:
        with self._lock:
            return sorted(self._templates.values(), key=lambda template: template.name)


_prompt_template_manager = PromptTemplateManager()


def get_prompt_template_manager() -> PromptTemplateManager:
    return _prompt_template_manager


router = APIRouter(prefix="/ai/prompts", tags=["prompt-templates"])


@router.post("", status_code=201)
def create_template_endpoint(
    payload: dict = Body(default={}),
    manager: PromptTemplateManager = Depends(get_prompt_template_manager),
) -> dict:
    try:
        template = manager.create(
            payload.get("name", ""),
            payload.get("text", ""),
            [TemplateVariable.from_dict(item) for item in payload.get("variables", [])],
        )
    except TemplateAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except (ValueError, TemplateValidationError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return template.to_dict()


@router.get("")
def list_templates_endpoint(
    manager: PromptTemplateManager = Depends(get_prompt_template_manager),
) -> list:
    return [template.to_dict() for template in manager.list_templates()]


@router.get("/{template}")
def get_template_endpoint(
    template: str,
    manager: PromptTemplateManager = Depends(get_prompt_template_manager),
) -> dict:
    try:
        return manager.get(template).to_dict()
    except UnknownTemplateError:
        raise HTTPException(status_code=404, detail="unknown template")


@router.delete("/{template}", status_code=204)
def delete_template_endpoint(
    template: str,
    manager: PromptTemplateManager = Depends(get_prompt_template_manager),
) -> None:
    try:
        manager.delete(template)
    except UnknownTemplateError:
        raise HTTPException(status_code=404, detail="unknown template")
