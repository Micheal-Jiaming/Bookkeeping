"""Category assignment.

Precedence, strongest first:

1. **Manual** -- whatever the user set by hand is never overwritten.
2. **Rules** -- deterministic keyword/regex rules on the item description, then
   on the merchant. Rules win over the model because they are auditable and
   repeatable: if "GREAT VALUE" means Groceries today it means Groceries next
   month, whereas a model may drift between runs.
3. **Model** -- the category the vision model suggested for that line, accepted
   only if it names a category that actually exists.
4. **Default** -- "Uncategorized", so nothing silently vanishes from reports.

This ordering follows the same rules-then-AI shape Firefly III uses for its
transaction rules and Receipt Wrangler for its category grants.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

DEFAULT_CATEGORY = "Uncategorized"


@dataclass(frozen=True)
class Rule:
    id: int
    field: str
    match_type: str
    pattern: str
    category_id: int
    priority: int


def load_rules(db: sqlite3.Connection) -> list[Rule]:
    rows = db.execute(
        "SELECT id, field, match_type, pattern, category_id, priority "
        "FROM category_rule WHERE enabled = 1 ORDER BY priority ASC, id ASC"
    ).fetchall()
    return [Rule(**row) for row in rows]


def _matches(rule: Rule, text: str) -> bool:
    if not text:
        return False
    if rule.match_type == "regex":
        try:
            return re.search(rule.pattern, text, re.IGNORECASE) is not None
        except re.error:
            # A user typed a broken regex. Ignore the rule rather than break
            # every scan until they fix it.
            return False
    return rule.pattern.strip().upper() in text.upper()


def match_rules(
    rules: list[Rule], description: str, merchant: str = ""
) -> tuple[int | None, int | None]:
    """First matching rule for this line. Returns (category_id, rule_id)."""
    for rule in rules:
        haystack = description if rule.field == "description" else merchant
        if _matches(rule, haystack):
            return rule.category_id, rule.id
    return None, None


def resolve_category(
    rules: list[Rule],
    category_ids_by_name: dict[str, int],
    *,
    description: str,
    merchant: str,
    model_suggestion: str | None,
) -> tuple[int | None, str]:
    """Decide one line's category. Returns (category_id, source)."""
    category_id, _ = match_rules(rules, description, merchant)
    if category_id is not None:
        return category_id, "rule"

    if model_suggestion:
        suggested = category_ids_by_name.get(model_suggestion.strip().lower())
        if suggested is not None:
            return suggested, "model"

    default = category_ids_by_name.get(DEFAULT_CATEGORY.lower())
    return default, "default"


def category_index(db: sqlite3.Connection) -> dict[str, int]:
    """Lower-cased category name -> id, for matching model output by name."""
    return {
        row["name"].lower(): row["id"]
        for row in db.execute("SELECT id, name FROM category").fetchall()
    }


def category_names(db: sqlite3.Connection) -> list[str]:
    """Category names offered to the model, in display order.

    'Uncategorized' is withheld deliberately: it is this application's marker
    for "nothing decided", and offering it invites the model to use it as an
    easy out instead of committing to a real category.
    """
    rows = db.execute(
        "SELECT name FROM category WHERE name <> 'Uncategorized' "
        "ORDER BY sort_order ASC, name ASC"
    ).fetchall()
    return [row["name"] for row in rows]
