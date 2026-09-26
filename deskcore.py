"""Pure helpers shared by the store and its feature modules. No database access."""
import re
from datetime import datetime, timezone
from uuid import uuid4


class Conflict(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def ident(prefix):
    return prefix + uuid4().hex[:12]


def text(value, label, limit=12000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f'{label} must contain 1–{limit} characters')
    return value.strip()


def scopes(values):
    if not isinstance(values, list) or not values or len(values) > 100:
        raise ValueError('Supply 1–100 relative file/directory paths or service:name resources')
    result = []
    for value in values:
        value = text(value, 'resource', 500).replace('\\', '/')
        if value.startswith('service:'):
            if not re.fullmatch(r'service:[a-zA-Z0-9_.:-]+', value):
                raise ValueError('Invalid service resource')
        else:
            if value.startswith('/') or ':' in value or any(c in value for c in '*?'):
                raise ValueError('Use literal repo-relative paths, not absolute paths or globs')
            parts = value.split('/')
            if '..' in parts:
                raise ValueError('Parent traversal is not a valid resource')
            value = '/'.join(p for p in parts if p and p != '.') or '.'
        result.append(value)
    return sorted(set(result))


def overlaps(a, b):
    if a.startswith('service:') or b.startswith('service:'):
        return a == b
    return a == '.' or b == '.' or a == b or a.startswith(b + '/') or b.startswith(a + '/')


# The human owner's identity on the board. 'rohan' is the id this project
# originally shipped with; it stays as the stored value so existing messages and
# receipts keep resolving. Clients should send 'human', which is aliased to it.
HUMAN = 'rohan'
