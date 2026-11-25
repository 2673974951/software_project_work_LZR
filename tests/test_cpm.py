"""cpm 模块单元测试：手算可验证的小网络."""
import numpy as np
import pytest

from rcpsp.cpm import compute_cpm
from rcpsp.project import Project, Task


def build(spec):
    tasks = {k: Task(k, k, "测试", d, tuple(p)) for k, (d, p) in spec.items()}
    return Project("t", tasks)


def test_known_network_times():
    # A(2) -> B(3) -> D(2)
    #   \-> C(1) -/
    proj = build({"A": (2, []), "B": (3, ["A"]), "C": (1, ["A"]), "D": (2, ["B", "C"])})
    r = compute_cpm(proj)
    i = {t: k for k, t in enumerate(r.task_ids)}
    assert r.project_duration == pytest.approx(7.0)
    assert r.es[i["A"]] == 0 and r.ef[i["A"]] == 2
    assert r.es[i["B"]] == 2 and r.ef[i["B"]] == 5
    assert r.es[i["C"]] == 2 and r.ef[i["C"]] == 3
    assert r.es[i["D"]] == 5 and r.ef[i["D"]] == 7
    assert r.total_float[i["C"]] == pytest.approx(2.0)
    assert r.free_float[i["C"]] == pytest.approx(2.0)
    assert r.critical_path == ["A", "B", "D"]


def test_parallel_tasks_have_float():
    proj = build({"A": (5, []), "B": (3, []), "C": (1, [])})
    r = compute_cpm(proj)
    assert r.project_duration == 5
    i = {t: k for k, t in enumerate(r.task_ids)}
    assert r.total_float[i["B"]] == 2
    assert r.total_float[i["C"]] == 4


def test_critical_path_is_connected():
    from rcpsp.project import load_project
    proj = load_project("data")
    r = compute_cpm(proj)
    assert r.critical_path[0] == "T01"
    for a, b in zip(r.critical_path[:-1], r.critical_path[1:]):
        assert a in proj.tasks[b].predecessors


def test_zero_duration_rejected_downstream():
    proj = build({"A": (3, []), "B": (2, ["A"])})
    r = compute_cpm(proj)
    assert r.project_duration == 5
