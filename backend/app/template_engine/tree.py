"""Дерево пунктов шаблона для UI и движков (анализ, валидатор, рендер)."""

from dataclasses import dataclass, field

from app.template_engine.validate import match


@dataclass
class TreeNode:
    id: str
    title: str
    type: str  # section | subsection | item
    kind: str | None
    requirement: str | None = None
    expert_role: str | None = None
    inputs: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)  # раскрытые маски → id пунктов
    requires_reference: list[str] = field(default_factory=list)
    output_hints: dict = field(default_factory=dict)
    conditional: str | None = None
    extension: bool = False
    optional: bool = False  # выключен в шаблоне по умолчанию (enabled: false)
    enabled: bool = True  # с учётом флагов проекта
    children: list["TreeNode"] = field(default_factory=list)

    def as_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k != "children"}
        d["children"] = [c.as_dict() for c in self.children]
        return d


def build_tree(template: dict, enabled_optional: list[str] | tuple[str, ...] = ()) -> list[TreeNode]:
    item_ids: list[str] = []

    def collect(node: dict) -> None:
        children = node.get("subsections") or node.get("items")
        if children:
            for c in children:
                collect(c)
        else:
            item_ids.append(str(node["id"]))

    for s in template["sections"]:
        collect(s)

    def expand(patterns: list[str], own_id: str) -> list[str]:
        out: list[str] = []
        for p in patterns:
            for t in match(p, item_ids):
                if t != own_id and t not in out:
                    out.append(t)
        return out

    def make(node: dict, type_: str, parent_enabled: bool, parent_optional: bool) -> TreeNode:
        node_id = str(node["id"])
        optional = parent_optional or node.get("enabled", True) is False
        enabled = parent_enabled and (node.get("enabled", True) is not False or node_id in enabled_optional)
        tn = TreeNode(
            id=node_id,
            title=node["title"],
            type=type_,
            kind=node.get("kind"),
            requirement=node.get("requirement"),
            expert_role=node.get("expert_role"),
            inputs=list(node.get("inputs", [])),
            tools=list(node.get("tools", [])),
            depends_on=expand(node.get("depends_on", []), node_id),
            requires_reference=list(node.get("requires_reference", [])),
            output_hints=dict(node.get("output_hints", {})),
            conditional=node.get("conditional"),
            extension=bool(node.get("extension", False)),
            optional=optional,
            enabled=enabled,
        )
        if node.get("subsections"):
            tn.children = [make(c, "subsection", enabled, optional) for c in node["subsections"]]
        elif node.get("items"):
            tn.children = [make(c, "item", enabled, optional) for c in node["items"]]
        return tn

    return [make(s, "section", True, False) for s in template["sections"]]


def count_items(tree: list[TreeNode], only_enabled: bool = True) -> int:
    total = 0
    for n in tree:
        if n.children:
            total += count_items(n.children, only_enabled)
        elif not only_enabled or n.enabled:
            total += 1
    return total
