"""Bounded card listings, independent of per-record compressed history pages."""
import json
import math
from contextlib import closing

CARD_WINDOW = 10


def bounded_listing(db, *, table, columns, terminal, clock, scope=None, where='1', params=(),
                    active_order='rowid', limit=CARD_WINDOW, offset=0, through=None, older=False):
    """All matching unfinished rows plus a SQL-limited terminal window.

    A through cursor preserves the explicitly opened range across refreshes. Its
    timestamp/rowid boundary survives the boundary record being continued/reopened.
    Only Load older extends that range, by one page. Offset is for manager reads.
    """
    if offset < 0:
        raise ValueError('Offset must be nonnegative')
    boundary = None
    if through:
        try:
            kind, project, stamp, position = json.loads(through)
            if (kind != table or project != scope or not isinstance(stamp, (int, float))
                    or not math.isfinite(stamp) or not isinstance(position, int) or position < 1):
                raise ValueError()
            boundary = (stamp, position)
        except (TypeError, ValueError, OverflowError):
            raise ValueError('Invalid listing cursor for this scope') from None
    predicate = f'({where})' + (' AND project_id=?' if scope else '')
    values = (*params, *((scope,) if scope else ()))
    key = f'({clock},rowid)'
    selected = f'{columns},{clock} AS _listing_time,rowid AS _listing_position'
    with closing(db.connect()) as conn:
        conn.execute('BEGIN')  # Active rows and terminal pages share one read snapshot.
        def read(condition, extra=(), suffix=''):
            return [dict(row) for row in conn.execute(
                f'SELECT {selected} FROM {table} WHERE {predicate} AND {condition} {suffix}', (*values, *extra))]
        active = read(f'NOT ({terminal})', suffix=f'ORDER BY {active_order}') if offset == 0 else []
        prefix = read(f'({terminal}) AND {key}>= (?,?)', boundary,
                      f'ORDER BY {clock} DESC,rowid DESC') if boundary else []
        tail = read(f'({terminal})' + (f' AND {key}< (?,?)' if boundary else ''),
                    (*(() if boundary is None else boundary), (limit + 1 if not boundary or older else 1), offset),
                    f'ORDER BY {clock} DESC,rowid DESC LIMIT ? OFFSET ?')
    take = limit if not boundary or older else 0
    finished = prefix + tail[:take]
    has_more = len(tail) > take
    cursor = (json.dumps([table, scope, finished[-1]['_listing_time'], finished[-1]['_listing_position']])
              if finished else through)
    for row in active + finished:
        row.pop('_listing_time')
        row.pop('_listing_position')
    return {'rows': active + finished, 'cursor': cursor, 'has_more': has_more,
            'next_offset': offset + limit if has_more else None}
