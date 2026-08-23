"""Category assignment.

Precedence, strongest first:

1. **Manual** -- whatever the user set by hand is never overwritten.
2. **Description rules** -- deterministic keyword/regex rules on the item name.
   These beat the model because they are auditable and repeatable: if
   "GREAT VALUE" means Groceries today it means Groceries next month, whereas a
   model may drift between runs.
3. **Model** -- the category the vision model suggested for that line, accepted
   only if it names a category that actually exists.
4. **Merchant rules** -- "everything from this shop is Groceries". Deliberately
   *below* the model: a merchant rule is a coarse safety net for lines nothing
   else recognised, and letting it outrank the model would relabel a specific,
   correct per-item judgement ("SOURDOUGH BOULE" → Dining) with a blanket store
   default. This ordering is the whole reason merchant rules are seeded with a
   high priority number.
5. **Default** -- "Uncategorized", so nothing silently vanishes from reports.

This follows the same rules-then-AI shape Firefly III uses for its transaction
rules and Receipt Wrangler for its category grants.
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
    rules: list[Rule], description: str, merchant: str = "", field: str | None = None
) -> tuple[int | None, int | None]:
    """First matching rule for this line. Returns (category_id, rule_id).

    ``field`` restricts matching to rules on that field, which is how the
    precedence chain evaluates description rules before the model and merchant
    rules after it.
    """
    for rule in rules:
        if field is not None and rule.field != field:
            continue
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
    """Decide one line's category. Returns (category_id, source).

    Sources, in the order they are tried: ``rule`` (a description rule),
    ``model``, ``merchant`` (a merchant rule), ``default``.
    """
    category_id, _ = match_rules(rules, description, merchant, field="description")
    if category_id is not None:
        return category_id, "rule"

    if model_suggestion:
        suggested = category_ids_by_name.get(model_suggestion.strip().lower())
        if suggested is not None:
            return suggested, "model"

    category_id, _ = match_rules(rules, description, merchant, field="merchant")
    if category_id is not None:
        return category_id, "merchant"

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
