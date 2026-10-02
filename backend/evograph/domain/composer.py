"""Small, versioned rich-input contract shared by HTTP and desktop admission."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from .models import Model

ReferenceKind = Literal[
    "milestone",
    "source_milestone",
    "architecture_component",
    "source_component",
    "attachment",
    "repository",
]


class ComposerText(Model):
    type: Literal["text"]
    text: str = Field(max_length=16000)


class ComposerReference(Model):
    type: Literal["reference"]
    kind: ReferenceKind
    id: str = Field(min_length=1, max_length=1024)
    project_id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=1024)

    def plain_text(self):
        prefix = "@" if self.kind in {"attachment", "repository"} else "#"
        return prefix + self.label


ComposerPart = Annotated[ComposerText | ComposerReference, Field(discriminator="type")]


class ComposerDocument(Model):
    version: Literal[1]
    parts: list[ComposerPart] = Field(max_length=512)

    def plain_text(self):
        return "".join(
            part.text if isinstance(part, ComposerText) else part.plain_text()
            for part in self.parts
        )

    def references(self):
        return [part for part in self.parts if isinstance(part, ComposerReference)]

    @model_validator(mode="after")
    def bounded(self):
        if len(self.references()) > 32:
            raise ValueError("每条消息最多包含 32 个对象引用")
        if len(self.plain_text()) > 16000:
            raise ValueError("消息与引用名称合计不能超过 16000 字符")
        return self
