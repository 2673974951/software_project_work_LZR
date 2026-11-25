"""sgs 模块单元测试：调度可行性是核心不变量."""
import numpy as np
import pytest

from rcpsp.cpm import compute_cpm
from rcpsp.project import Project, Resource, Task
from rcpsp.rules import PRIORITY_RULES
from rcpsp.sgs import double_justify, parallel_sgs, random_multistart, serial_sgs


def build(spec, roles, demands):
    tasks = {k: Task(k, k, "测试", d, tuple(p)) for k, (d, p) in spec.items()}
    resources = {r: Resource(r, r, c) for r, c in roles.items()}
    return Project("t", tasks, resources, demands)


def check_feasible(proj, sch, tol=1e-9):
    """校验依赖约束与资源容量约束。"""
    idx = {t: i for i, t in enumerate(sch.task_ids)}
    for tid in sch.task_ids:
        i = idx[tid]
        for p in proj.tasks[tid].predecessors:
            assert sch.starts[i] >= sch.finishes[idx[p]] - tol, "依赖被违反：%s" % tid
    for role in proj.resources:
        cap = proj.resources[role].capacity
        prof = sch.profiles[role]
        assert prof.max() <= cap + tol, "角色 %s 超载" % role


SMALL = (
    {"A": (3, []), "B": (2, ["A"]), "C": (4, ["A"]), "D": (2, ["B", "C"])},
    {"R": 2},
    {"A": {"R": 1}, "B": {"R": 1}, "C": {"R": 2}, "D": {"R": 1}},
)


def test_serial_sgs_is_feasible_for_all_rules():
    proj = build(*SMALL)
    for rule in PRIORITY_RULES:
        sch = serial_sgs(proj, rule=rule, rng=np.random.default_rng(0))
        check_feasible(proj, sch)


def test_parallel_sgs_is_feasible_for_all_rules():
    proj = build(*SMALL)
    for rule in PRIORITY_RULES:
        sch = parallel_sgs(proj, rule=rule, rng=np.random.default_rng(0))
        check_feasible(proj, sch)


def test_resource_conflict_extends_makespan():
    # 三个互不依赖的 2 天任务，只有 1 个人 -> 只能串行
    proj = build({"A": (2, []), "B": (2, []), "C": (2, [])},
                 {"R": 1}, {"A": {"R": 1}, "B": {"R": 1}, "C": {"R": 1}})
    assert compute_cpm(proj).project_duration == 2
    sch = serial_sgs(proj, rule="LFT")
    assert sch.makespan == 6
    check_feasible(proj, sch)


def test_no_conflict_when_capacity_is_enough():
    proj = build({"A": (2, []), "B": (2, []), "C": (2, [])},
                 {"R": 3}, {"A": {"R": 1}, "B": {"R": 1}, "C": {"R": 1}})
    assert serial_sgs(proj, rule="LFT").makespan == 2


def test_makespan_never_below_cpm():
    from rcpsp.project import load_project
    proj = load_project("data")
    cpm = compute_cpm(proj).project_duration
    for rule in PRIORITY_RULES:
        assert serial_sgs(proj, rule=rule, rng=np.random.default_rng(0)).makespan >= cpm


def test_starts_are_active_no_gratuitous_idle():
    """活动调度：任一任务都不能在不违反约束的前提下更早开工。

    这里用一个弱化判据：对每个任务，若把它单独提前一天，必然与资源或
    依赖冲突。
    """
    from rcpsp.project import load_project
    proj = load_project("data")
    sch = serial_sgs(proj, rule="LFT")
    idx = {t: i for i, t in enumerate(sch.task_ids)}
    roles, mat = proj.demand_matrix()
    caps = proj.capacity if hasattr(proj, "capacity") else proj.capacities()
    for i, tid in enumerate(sch.task_ids):
        if sch.starts[i] == 0:
            continue
        t = int(sch.starts[i]) - 1
        d = int(sch.finishes[i] - sch.starts[i])
        prec_ok = all(sch.finishes[idx[p]] <= t for p in proj.tasks[tid].predecessors)
        res_ok = True
        for j, role in enumerate(roles):
            if mat[i, j] == 0:
                continue
            occ = np.zeros(d, dtype=int)
            for k, other in enumerate(sch.task_ids):
                if k == i:
                    continue
                s, f = int(sch.starts[k]), int(sch.finishes[k])
                for tt in range(max(s, t), min(f, t + d)):
                    occ[tt - t] += mat[k, j]
            if np.any(occ + mat[i, j] > caps[j]):
                res_ok = False
                break
        assert not (prec_ok and res_ok), "任务 %s 本可以更早开工，调度不是活动的" % tid


def test_double_justify_never_worsens():
    from rcpsp.project import load_project
    proj = load_project("data")
    for rule in ("LFT", "SPT", "RANDOM"):
        s0 = serial_sgs(proj, rule=rule, rng=np.random.default_rng(1))
        s1, hist = double_justify(proj, s0)
        assert s1.makespan <= s0.makespan
        check_feasible(proj, s1)


def test_random_multistart_is_reproducible_and_feasible():
    from rcpsp.project import load_project
    proj = load_project("data")
    a, _ = random_multistart(proj, n_samples=30, seed=7, improve=False)
    b, _ = random_multistart(proj, n_samples=30, seed=7, improve=False)
    assert a.makespan == b.makespan
    check_feasible(proj, a)
