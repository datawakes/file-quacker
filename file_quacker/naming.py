"""Target-name cleanup shared by the SQL Server export and the DDL panel,
so both turn `claim.id` into the same bare `claim_id`."""

from __future__ import annotations

import re


def snake_case(name: str) -> str:
    """Replace space/dot/hyphen with ``_``, split on CamelCase
    transitions, lowercase, collapse repeated underscores, and strip
    leading/trailing ``_``.  Acronyms stay glued together
    (``Patient_ID`` → ``patient_id``).  Characters like ``$`` or ``()``
    pass through and get bracket-quoted in the emitted SQL.  A name made
    only of separators falls back to ``col``.
    """
    name = re.sub(r'[ .-]', '_', name)
    if not name.isupper():
        name = re.sub(r'(?<!^)(?<!_)(?=[A-Z][a-z])|(?<=[a-z])(?=[A-Z])', '_', name)
    name = name.lower()
    name = re.sub(r'_{2,}', '_', name)
    name = name.strip('_')
    return name or 'col'


def unique_name(base: str, taken: set[str]) -> str:
    """Return ``base`` if free; otherwise ``base_1`` / ``base_2`` / ... until
    a free name is found. Used to de-dupe target column names so SQL Server
    doesn't reject the CREATE TABLE."""
    if base not in taken:
        return base
    i = 1
    while f'{base}_{i}' in taken:
        i += 1
    return f'{base}_{i}'
