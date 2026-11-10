"""No price literal may live outside app/domain/pricing.py.

Scans every string constant (including the literal parts of f-strings, but
not docstrings) and every numeric constant in the application source.
Fails on:
  * the currency symbol anywhere — prices are rendered only via format_price();
  * a number written next to a currency marker ("45 zł", "55 PLN", "55 злотых");
  * a standalone current/previous price value inside a string ("до 45");
  * a numeric constant equal to a price, in złoty or grosze (55, 5500).

If this fails, route the value through get_current_price()/format_price()
instead of writing it down. Tests are deliberately out of scope.
"""
import ast
import re
from pathlib import Path

from app.domain import pricing

ROOT = Path(__file__).resolve().parent.parent
PRICING_MODULE = ROOT / "app" / "domain" / "pricing.py"
SOURCES = [p for p in [*sorted((ROOT / "app").rglob("*.py")), ROOT / "main.py"] if p != PRICING_MODULE]

PRICES = {pricing._PRICE_BEFORE_CUTOFF, pricing._PRICE_AFTER_CUTOFF}
PRICE_NUMBERS = PRICES | {p * 100 for p in PRICES}
_CURRENCY = r"(zł|zl\b|pln\b|злот)"
_NUMBER_WITH_CURRENCY = re.compile(rf"\d\s*{_CURRENCY}|{_CURRENCY}\s*\d", re.IGNORECASE)
_STANDALONE_PRICE = re.compile(r"(?<!\d)(?:" + "|".join(map(str, sorted(PRICES))) + r")(?!\d)")


def _docstring_ids(tree: ast.AST) -> set[int]:
    """Docstrings are developer documentation, not user-facing text."""
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                ids.add(id(first.value))
    return ids


def _violations(path: Path, root: Path = ROOT) -> list[str]:
    found = []
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    docstrings = _docstring_ids(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or id(node) in docstrings:
            continue
        where = f"{path.relative_to(root)}:{node.lineno}"
        value = node.value
        if isinstance(value, str):
            if "zł" in value:
                found.append(f"{where}: currency symbol outside format_price(): {value!r}")
            elif _NUMBER_WITH_CURRENCY.search(value):
                found.append(f"{where}: number with currency: {value!r}")
            elif _STANDALONE_PRICE.search(value):
                found.append(f"{where}: price value in string: {value!r}")
        elif isinstance(value, (int, float)) and not isinstance(value, bool) and value in PRICE_NUMBERS:
            found.append(f"{where}: numeric price literal {value!r}")
    return found


def test_sources_were_found():
    assert len(SOURCES) > 10
    assert ROOT / "app" / "handlers.py" in SOURCES


def test_no_price_literal_outside_pricing_module():
    violations = [v for path in SOURCES for v in _violations(path)]
    assert not violations, "Price literals outside app/domain/pricing.py:\n" + "\n".join(violations)


def test_detector_catches_known_bad_patterns(tmp_path):
    before, after = sorted(PRICES)
    bad = tmp_path / "bad.py"
    bad.write_text(
        "a = f'Оплатить {x} zł'\n"
        f"b = 'Стоимость {after} PLN'\n"
        f"c = 'скидка до {before} до октября'\n"
        f"d = {after * 100}\n"
        "ok = 'telegram_id 123456789, 30 дней'\n"
        "def f():\n"
        f"    '''Docstring may mention {after} zł.'''\n",
        encoding="utf-8",
    )
    lines = sorted(int(v.split(":")[1]) for v in _violations(bad, root=tmp_path))
    assert lines == [1, 2, 3, 4]
