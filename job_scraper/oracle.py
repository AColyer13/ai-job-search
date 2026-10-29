"""Load and check the primary oracle: research/master_profile.json, the facts Adam has confirmed about himself.

The file is gitignored (personal data), so every caller must cope with `load` returning None.
"""
import json
from pathlib import Path

ORACLE_PATH = Path(__file__).resolve().parent.parent / "research" / "master_profile.json"
REQUIRED = {
    "identity": ("full_name", "email", "phone", "location", "linkedin", "github", "portfolio"),
    "work_authorization": ("authorized_us", "needs_sponsorship"),
}


class OracleError(ValueError):
    pass


def validate(data):
    """Raise OracleError when the oracle is missing a section, role field or a valid banned-claim pattern."""
    import re
    for section, keys in REQUIRED.items():
        block = data.get(section)
        if not isinstance(block, dict):
            raise OracleError(f"missing section: {section}")
        for key in keys:
            if key not in block:
                raise OracleError(f"missing {section}.{key}")
    if not data.get("roles"):
        raise OracleError("missing roles")
    for role in data["roles"]:
        for key in ("title", "employer", "start", "end"):
            if not role.get(key):
                raise OracleError(f"role {role.get('employer', '?')} is missing {key}")
    for claim in data.get("banned_claims", []):
        try:
            re.compile(claim["pattern"])
        except (KeyError, re.error) as e:
            raise OracleError(f"bad banned claim {claim!r}: {e}") from None
    return data


def load(path=None):
    """The validated oracle, or None when the file is absent (a fresh clone has none)."""
    path = Path(path or ORACLE_PATH)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except ValueError as e:
        raise OracleError(f"{path.name} is not valid JSON: {e}") from None
    return validate(data)
