"""A persisted shuffle bag: one draw per day, no repeats until the bag is empty."""
from copy import deepcopy
from datetime import date
import re
from secrets import SystemRandom

ID = re.compile(r'[a-z][a-z0-9_-]{0,63}')


def choose(lines, stored, day, rng=None):
    date.fromisoformat(day)
    state = deepcopy(stored) if stored is not None else {
        'version': 1, 'day': '', 'current': None, 'last': None, 'seen': [], 'remaining': []}
    if not isinstance(state, dict) or set(state) != {'version', 'day', 'current', 'last', 'seen', 'remaining'} or state['version'] != 1:
        raise ValueError('Invalid opening rotation')
    if not isinstance(state['day'], str) or (state['day'] and date.fromisoformat(state['day']).isoformat() != state['day']):
        raise ValueError('Invalid opening day')
    for key in ('current', 'last'):
        if state[key] is not None and (not isinstance(state[key], str) or not ID.fullmatch(state[key])):
            raise ValueError('Invalid opening selection')
    for key in ('seen', 'remaining'):
        ids = state[key]
        if not isinstance(ids, list) or len(ids) > 1000 or any(not isinstance(value, str) or not ID.fullmatch(value) for value in ids) or len(ids) != len(set(ids)):
            raise ValueError('Invalid opening bag')
    if set(state['seen']) & set(state['remaining']):
        raise ValueError('Overlapping opening bag')
    catalog = {line['id']: line['text'] for line in lines}
    rng = rng or SystemRandom()
    state['seen'] = [value for value in state['seen'] if value in catalog]
    state['remaining'] = [value for value in state['remaining'] if value in catalog]
    additions = set(catalog) - set(state['seen']) - set(state['remaining'])
    if additions:
        state['remaining'].extend(sorted(additions))
        rng.shuffle(state['remaining'])
    if not catalog:
        state.update(day=day, current=None, seen=[], remaining=[])
    elif state['day'] != day or state['current'] not in catalog:
        if not state['remaining']:
            state['remaining'] = list(catalog)
            state['seen'] = []
            rng.shuffle(state['remaining'])
        # The next round also avoids repeating the immediately previous sentence.
        if len(state['remaining']) > 1 and state['remaining'][-1] == state['last']:
            state['remaining'][0], state['remaining'][-1] = state['remaining'][-1], state['remaining'][0]
        selected = state['remaining'].pop()
        state['seen'].append(selected)
        state.update(day=day, current=selected, last=selected)
    return {'key': day, 'id': state['current'], 'text': catalog.get(state['current'], '')}, state
