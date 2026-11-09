"""Exact, deliberately narrow grammar for count/type/city faculty-URL requests.

Unmatched wording goes through the existing semantic planner. Never partially
parse a request: doing so could silently discard exclusions or person filters.
"""
import re
from .prompt_discovery import Plan, Criterion

_PATTERN = re.compile(
    r"\s*(?:find|list|show|I\s+want|I\s+need)\s+"
    r"(?P<count>[1-9][0-9]{0,3}?)\s+"
    r"(?P<kind>(?:(?:engineering|medical|dental|law|pharmacy|nursing|business|arts|science)\s+)?"
    r"(?:colleges?|universities|university|institutes?))\s+"
    r"(?:in|from|located\s+in)\s+"
    r"(?P<city>[^\W\d_]+(?:[ '\-][^\W\d_]+){0,3}?)\s+"
    r"(?:and\s+(?:return|give|provide|show|list)\s+)?"
    r"(?:(?:their|its|the)\s+)?(?:official\s+)?faculty\s+"
    r"(?:(?:directory|page)\s+)?URLs?\s*[.!]?\s*", re.I)
# Reject ambiguous modifiers in the location span. They require semantic planning.
_AMBIGUOUS = set('and or not no excluding except exclude only near around with without all any top best private public government autonomous having offering accredited research emails email students faculty'.split())


def simple_faculty_plan(prompt):
    match = _PATTERN.fullmatch(prompt)
    if match is None:
        return None
    city, kind = match['city'], match['kind']
    if set(re.findall(r"\w+", city.casefold())) & _AMBIGUOUS:
        return None
    return Plan(action='search', scope='campus_staff', objective=prompt,
        requested_institutions=int(match['count']), requested_people=None,
        requested_people_per_institution=None, target='faculty',
        criteria=[Criterion(id='institution_type', requirement=kind,
                            prompt_quote=kind, applies_to='institution'),
                  Criterion(id='location', requirement='Located in ' + city,
                            prompt_quote=city, applies_to='institution')],
        queries=[f'{kind} {city} official faculty', f'{kind} {city} departments faculty',
                 f'{kind} {city} official website'],
        explanation='Parsed a complete count/type/city faculty-URL request using the bounded grammar.')
