"""Explicit task-only policies for the frozen four-candidate comparison."""

from dataclasses import dataclass

from backend.domain.recommendation.selection_context import SelectionContext

TARGETS = ("ease", "ease-bpr-rrf", "ease-bpr-mmr0.8", "ease-bpr-mmr0.5")


@dataclass(frozen=True)
class TaskPolicy:
    name: str
    new_target: str
    repeat_target: str

    def __post_init__(self):
        if not self.name or self.new_target not in TARGETS or self.repeat_target not in TARGETS:
            raise ValueError("An explicit name and two registered targets are required")

    def choose(self, context: SelectionContext) -> str:
        return self.new_target if context.task == "new" else self.repeat_target


def comparison_policies(new_target, repeat_target):
    return tuple(TaskPolicy(t, t, t) for t in TARGETS) + (
        TaskPolicy("task-rule", new_target, repeat_target),
    )
