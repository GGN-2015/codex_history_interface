from dataclasses import dataclass
import re
from typing import Callable


@dataclass(frozen=True)
class EquationLabel:
    name: str
    target: str
    message: int
    order: int


@dataclass(frozen=True)
class EquationReference:
    name: str
    command: str
    message: int
    order: int
    context: str
    placeholder: str


class EquationReferences:
    def __init__(self) -> None:
        self.labels: dict[str, list[EquationLabel]] = {}
        self.references: list[EquationReference] = []
        self.unresolved: dict[str, list[str]] = {}
        self.order = 0
        self.label_count = 0

    def register(self, source: str, message: int, context: str) -> str:
        pattern = re.compile(r"%[^\r\n]*|\\(label|eqref|ref)\s*\{([A-Za-z0-9:._/-]+)\}|\\(?:[A-Za-z@]+|[\s\S])")

        def replace(match):
            command, name = match.groups()
            if command is None:
                return match.group()
            self.order += 1
            if command == "label":
                self.label_count += 1
                target = "codex-eq-" + str(self.label_count)
                self.labels.setdefault(name, []).append(EquationLabel(name, target, message, self.order))
                return r"\label{" + target + "}"
            placeholder = r"\CodexPendingReference{" + str(len(self.references)) + "}"
            self.references.append(EquationReference(name, command, message, self.order, context, placeholder))
            return placeholder

        return pattern.sub(replace, source)

    def resolve(self, source: str, *, escape: Callable[[str], str], warn: Callable[[str], None]) -> str:
        replacements = {}
        self.unresolved = {}
        for reference in self.references:
            candidates = self.labels.get(reference.name, [])
            local = [label for label in candidates if label.message == reference.message]
            preferred = local or candidates
            preceding = [label for label in preferred if label.order < reference.order]
            if preferred:
                target = (preceding[-1] if preceding else preferred[0]).target
                replacement = "\\" + reference.command + "{" + target + "}"
            else:
                self.unresolved.setdefault(reference.name, []).append(reference.context)
                replacement = r"\text{" + escape("[未解析的公式引用：" + reference.name + "]") + "}"
                warn(reference.context + ": equation reference '" + reference.name + "' has no exported target; its identifier was preserved")
            replacements[reference.placeholder] = replacement
        return re.sub(r"\\CodexPendingReference\{\d+\}", lambda match: replacements[match.group()], source)
