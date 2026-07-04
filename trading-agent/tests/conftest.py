import sys

import pytest


@pytest.fixture(autouse=True)
def cleanup_lightweight_module_stubs():
    """
    Some legacy tests install lightweight numpy/pandas stubs into sys.modules.
    Remove those after each test so later imports of scipy/pandas see the real
    packages from the venv.
    """
    yield
    for name in ("numpy", "pandas"):
        mod = sys.modules.get(name)
        if mod is not None and not hasattr(mod, "__version__"):
            sys.modules.pop(name, None)
    db_ops = sys.modules.get("database.operations")
    if db_ops is not None and not hasattr(db_ops, "get_current_portfolio_value"):
        for name in ("database.operations", "database.schema", "database"):
            sys.modules.pop(name, None)
    sqlalchemy = sys.modules.get("sqlalchemy")
    if sqlalchemy is not None and not hasattr(sqlalchemy, "Enum"):
        for name in list(sys.modules):
            if name == "sqlalchemy" or name.startswith("sqlalchemy."):
                sys.modules.pop(name, None)
    pytz = sys.modules.get("pytz")
    if pytz is not None and not hasattr(pytz, "all_timezones"):
        sys.modules.pop("pytz", None)
