"""metrics 模块单元测试."""
import numpy as np
import pytest

from rcpsp.cpm import compute_cpm
from rcpsp.metrics import (
    compare_rules,
    critical_sequence,
    lower_bounds,
    resource_conflict_report,
    resource_utilisation_table,
    schedule_metrics,
)
from rcpsp.project import Project, Resource, Task
from rcpsp.sgs import serial_sgs


def build(spec, roles, demands):
    tasks = {k: Task(k, k, "测试", d, tuple(p)) for k, (d, p) in spec.items()}
    resources = {r: Resource(r, r, c) for r, c in roles.items()}
    return Project("t", tasks, resources, demands)


def test_utilisation_between_zero_and_one():
    proj = build({"A": (2, []), "B": (2, ["A"])}, {"R": 2}, {"A": {"R": 2}, "B": {"R": 1}})
    sch = serial_sgs(proj, rule="LFT")
    for row in resource_utilisation_table(proj, sch):
        assert 0.0 <= row["utilisation"] <= 1.0
        assert row["busy_person_days"] <= row["capacity_person_days"] + 1e-9


def test_conflict_detected_when_overloaded():
    # 三个并行任务各需 2 人，容量只有 2 -> 无约束计划必然冲突
    proj = build({"A": (3, []), "B": (3, []), "C": (3, [])},
                 {"R": 2}, {"A": {"R": 2}, "B": {"R": 2}, "C": {"R": 2}})
    rows = resource_conflict_report(proj)
    r = [x for x in rows if x["role_id"] == "R"][0]
    assert r["has_conflict"]
    assert r["peak_demand"] == 6
    assert r["max_overload"] == 4


def test_no_conflict_when_capacity_sufficient():
    proj = build({"A": (3, []), "B": (3, [])}, {"R": 4}, {"A": {"R": 2}, "B": {"R": 2}})
    r = [x for x in resource_conflict_report(proj) if x["role_id"] == "R"][0]
    assert not r["has_conflict"]
    assert r["conflict_days"] == 0


def test_critical_sequence_subset_of_tasks():
    from rcpsp.project import load_project
    proj = load_project("data")
    sch = serial_sgs(proj, rule="LFT")
    cs = critical_sequence(proj, sch)
    assert set(cs) <= set(sch.task_ids)
    assert len(cs) >= 1


def test_lower_bounds():
    proj = build({"A": (4, [])}, {"R": 2}, {"A": {"R": 2}})
    lb = lower_bounds(proj)
    assert lb["cpm"] == 4
    assert lb["lb"] >= lb["cpm"]


def test_compare_rules_sorted_by_makespan():
    from rcpsp.project import load_project
    proj = load_project("data")
    rows = compare_rules(proj, ["LFT", "SPT", "RANDOM"], method="serial")
    mk = [r["makespan"] for r in rows]
    assert mk == sorted(mk)
    assert all("schedule" in r for r in rows)


def test_schedule_metrics_keys():
    from rcpsp.project import load_project
    proj = load_project("data")
    sch = serial_sgs(proj, rule="LFT")
    m = schedule_metrics(proj, sch)
    for key in ("makespan", "mean_utilisation", "resource_delay", "critical_task_count"):
        assert key in m
    assert m["resource_delay"] >= 0
