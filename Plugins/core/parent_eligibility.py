# Part of: ProgTrack 0.3.0
# Required ProgTrack version: 0.2.3 or newer.
"""Pure biological-parent eligibility, shared by pickers and commands."""
from calendar import isleap
from .project_periods import _day, project_at_date, has_project_membership
from .identity_conventions import sex_token


def parent_eligible(target, candidate, slot, mode):
    """No actor permissions here: callers separately enforce Unit/write access."""
    if str(target.get('species') or '').strip().casefold() != str(candidate.get('species') or '').strip().casefold():
        return False
    if sex_token(candidate.get('sex')) != ('M' if slot in ('sperm_donor', 'surrogate_father') else 'F'):
        return False
    birth, parent_birth = _day(target.get('birth_date')), _day(candidate.get('birth_date'))
    if not birth or not parent_birth or parent_birth >= birth:
        return False
    if mode == 'embryo_transfer' and slot == 'sperm_donor':
        project_id = project_at_date(target, birth)
        return bool(project_id and has_project_membership(candidate, project_id))
    if mode == 'natural' and slot in ('egg_donor', 'sperm_donor'):
        raw = candidate.get('death_date') or candidate.get('sterbedatum')
        if not raw:
            return True
        death = _day(raw)
        if death is None:
            return False
        if slot == 'egg_donor':
            return death >= birth
        cutoff = birth.replace(year=birth.year - 1, day=28 if birth.month == 2 and birth.day == 29 and not isleap(birth.year - 1) else birth.day)
        return death > cutoff
    return True
