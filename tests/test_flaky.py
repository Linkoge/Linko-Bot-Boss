"""Failed-test reporting of the deploy suite: parsing, the rerun decision, the journal, repeat-offender To-Dos, the panel."""
import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest
from test_web import sign_in

from ai_manager import flaky
from ai_manager.todos import Todos

pytest_plugins = ['test_web']
ROOT = Path(__file__).resolve().parents[1]
XML = '''<testsuites><testsuite>
<testcase classname="tests.test_a" name="test_ok" file="tests/test_a.py"/>
<testcase classname="tests.test_a" name="test_bad" file="tests/test_a.py"><failure message="assert 1 == 2&#10;more">trace</failure></testcase>
<testcase classname="tests.test_b.TestC" name="test_err[x]" file="tests/test_b.py"><error message="">RuntimeError: boom
tail</error></testcase>
</testsuite></testsuites>'''


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_junit_failures_become_node_ids_with_one_line_errors():
    assert flaky.failures(XML) == [('tests/test_a.py::test_bad', 'assert 1 == 2'),
                                   ('tests/test_b.py::TestC::test_err[x]', 'RuntimeError: boom')]
    assert flaky.failures('not xml') == [] and flaky.failures('<testsuites/>') == []


def test_rerun_decision():
    first = [('a', 'e1'), ('b', 'e2')]
    assert flaky.decide(first, []) == (first, [])  # all pass on rerun: flaky
    assert flaky.decide(first, [('b', 'again')]) == ([('a', 'e1')], [('b', 'again')])
    assert flaky.decide(first, None) == ([], first)  # a rerun that reported nothing proves nothing


def test_journal_is_append_only_json_lines_and_fixes_follow(tmp_path):
    path = tmp_path / 'data' / flaky.JOURNAL_NAME
    flaky.append(path, [flaky.record('a', 'e', 'flaky', 'c1', now=1), flaky.record('b', 'e', 'real', 'c1', now=2)])
    flaky.append(path, [flaky.record('c', 'e', 'real', 'c2', now=3)])
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert [r['test'] for r in rows] == ['a', 'b', 'c'] and set(rows[0]) == {'time', 'commit', 'test', 'error', 'kind'}
    flaky.append(None, [rows[0]])  # no journal configured: nothing happens
    flaky.mark_fixed(path, 'fix1', now=4)
    rows = flaky.read(path)
    assert [(r['test'], r['fix_commit']) for r in rows if r['kind'] == 'fixed'] == [('b', 'fix1'), ('c', 'fix1')]
    flaky.mark_fixed(path, 'fix2', now=5)
    assert len(flaky.read(path)) == 5  # nothing open any more
    path.write_text(path.read_text() + 'garbage\n')
    assert len(flaky.read(path)) == 5


def test_top_counts_flaky_only_and_offenders_need_two_in_fourteen_days():
    now = time.time()
    day = 86400
    records = [flaky.record('a', 'e', 'flaky', now=now - 20 * day), flaky.record('a', 'e', 'flaky', now=now - day),
               flaky.record('b', 'e', 'flaky', now=now - day), flaky.record('b', 'e', 'flaky', now=now - 2 * day),
               flaky.record('b', 'e', 'flaky', now=now), flaky.record('c', 'e', 'real', now=now)]
    assert [(t['test'], t['count']) for t in flaky.top(records)] == [('b', 3), ('a', 2)]
    assert list(flaky.offenders(records, now)) == ['b']  # a's first record is older than 14 days


def test_todo_for_a_repeat_offender_is_created_once(client):
    browser, app, settings = client
    sign_in(browser, app, settings)
    project = app.state.registry.add(settings.project_roots[0] / 'one')['id']
    journal = settings.db_path.parent / flaky.JOURNAL_NAME
    now = time.time()
    flaky.append(journal, [flaky.record('tests/t.py::x', 'boom', 'flaky', 'abc1234567', now=now - 60)])
    assert flaky.sync_todos(app.state.db, Todos(app.state.db), project) == []  # once is not a pattern
    flaky.append(journal, [flaky.record('tests/t.py::x', 'boom', 'flaky', 'def1234567', now=now)])
    assert flaky.sync_todos(app.state.db, Todos(app.state.db), project) == ['Fix flaky test tests/t.py::x']
    assert flaky.sync_todos(app.state.db, Todos(app.state.db), project) == []
    [todo] = Todos(app.state.db).list(project)
    assert todo['kind'] == 'ready' and 'abc1234567' in todo['details'] and 'def1234567' in todo['details']
    Todos(app.state.db).archive([todo['id']])
    assert flaky.sync_todos(app.state.db, Todos(app.state.db), project) == []  # archived is not recreated
    top = browser.get('/api/flaky').json()['top']
    assert top == [{'test': 'tests/t.py::x', 'count': 2, 'last_seen': pytest.approx(now, abs=1)}]


def run_suite(tmp_path, monkeypatch, capsys, parallel_code, rerun_code, journal=True):
    """full_suite.main with fake phases: a script that writes junit XML and exits with the given code."""
    suite = load('full_suite')
    script = ('import sys\nargs=sys.argv\nxml=args[args.index("--junitxml")+1]\n'
              f'rerun="tests/test_a.py::test_bad" in args\ncode={rerun_code} if rerun else {parallel_code}\n'
              f'open(xml,"w").write({XML!r} if code else "<testsuites/>")\n'
              'print("1 failed, 3 passed" if code else "3 passed"); raise SystemExit(code)\n')
    fake = tmp_path / 'fake.py'
    fake.write_text(script)
    empty = [sys.executable, '-c', 'raise SystemExit(5)']
    monkeypatch.setattr(suite, 'phases', lambda args: [('parallel', [sys.executable, str(fake)]), ('serial', empty)])
    monkeypatch.setattr(suite, 'PYTEST', [str(fake)])  # the rerun command: python <fake> -p no:xdist <ids>
    if journal:
        monkeypatch.setenv(flaky.JOURNAL_ENV, str(tmp_path / 'journal.jsonl'))
        monkeypatch.setenv(flaky.COMMIT_ENV, 'abc123')
    code = suite.main([])
    return code, capsys.readouterr().out.splitlines(), flaky.read(tmp_path / 'journal.jsonl')


def test_a_failure_that_passes_on_the_serial_rerun_is_flaky_recorded_and_not_a_failure(tmp_path, monkeypatch, capsys):
    code, out, rows = run_suite(tmp_path, monkeypatch, capsys, parallel_code=1, rerun_code=0)
    assert code == 0 and any(line.startswith('Flaky tests') for line in out)
    assert out[-1].startswith('Full suite passed with 2 flaky test(s) recorded')
    assert sorted((r['test'], r['kind'], r['commit']) for r in rows) == [
        ('tests/test_a.py::test_bad', 'flaky', 'abc123'), ('tests/test_b.py::TestC::test_err[x]', 'flaky', 'abc123')]


def test_a_failure_that_fails_again_on_rerun_is_real_listed_and_fails(tmp_path, monkeypatch, capsys):
    code, out, rows = run_suite(tmp_path, monkeypatch, capsys, parallel_code=1, rerun_code=1)
    assert code == 1 and out[-1].startswith('Full suite failed: parallel phase: 1 failed, 3 passed')
    assert 'Failed tests:' in out and '  tests/test_a.py::test_bad - assert 1 == 2' in out
    assert {r['kind'] for r in rows} == {'real'}


def test_a_green_run_marks_open_real_failures_fixed_and_no_journal_means_no_file(tmp_path, monkeypatch, capsys):
    flaky.append(tmp_path / 'journal.jsonl', [flaky.record('tests/x.py::t', 'e', 'real', 'old', now=1)])
    code, out, rows = run_suite(tmp_path, monkeypatch, capsys, parallel_code=0, rerun_code=0)
    assert code == 0 and [(r['test'], r['kind'], r.get('fix_commit')) for r in rows] == [
        ('tests/x.py::t', 'real', None), ('tests/x.py::t', 'fixed', 'abc123')]
    fresh = tmp_path / 'fresh'
    fresh.mkdir()
    monkeypatch.delenv(flaky.JOURNAL_ENV)
    run_suite(fresh, monkeypatch, capsys, parallel_code=1, rerun_code=1, journal=False)
    assert not (fresh / 'journal.jsonl').exists()


def test_the_deploy_receipt_names_failed_and_flaky_tests():
    deploy = load('deploy')
    noisy = 'lots\nof\noutput\nFailed tests:\n  tests/a.py::t - boom\nFull suite failed: parallel phase: 1 failed, 9 passed'
    assert deploy.suite_report(noisy) == 'Failed tests:\n  tests/a.py::t - boom\nFull suite failed: parallel phase: 1 failed, 9 passed'
    ok = 'x\nFlaky tests (a):\n  tests/a.py::t - boom\nFull suite passed with 1 flaky test(s) recorded'
    assert deploy.suite_report(ok).splitlines()[0].startswith('Flaky tests')
    assert deploy.suite_report('no summary here') == 'no summary here'


def test_instances_have_no_flaky_api(tmp_path):
    source = (ROOT / 'ai_manager' / 'web.py').read_text()
    assert '"/api/flaky", dependencies=[Depends(require_owner), Depends(unrestricted)]' in source
