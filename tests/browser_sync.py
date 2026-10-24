"""Synchronisation helpers for the real-browser tests (not a test module)."""


def refreshed(page, call='refresh()'):
    """Refresh until one call renders: the app's own poll can supersede a refresh, which then returns false unrendered."""
    for _ in range(20):
        if page.evaluate(f'()=>{call}'):
            return
    raise AssertionError(f'every {call} was superseded')
