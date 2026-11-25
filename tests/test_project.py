"""project 模块单元测试：数据载入与 DAG 校验."""
import numpy as np
import pytest

from rcpsp.project import DataError, Project, Resource, Task, load_project


def build(spec, roles=None, demands=None, name="test"):
    tasks = {k: Task(k, k, "测试", d, tuple(p)) for k, (d, p) in spec.items()}
    resources = {r: Resource(r, r, c) for r, c in (roles or {}).items()}
    return Project(name, tasks, resources, demands or {})


def test_load_real_dataset():
    proj = load_project("data")
    assert len(proj) == 42
    assert len(proj.resources) == 10
    assert proj.order[0] == "T01"
    # 拓扑序必须满足所有依赖在前
    for tid in proj.order:
        for p in proj.tasks[tid].predecessors:
            assert proj.index[p] < proj.index[tid]


def test_cycle_is_detected():
    with pytest.raises(DataError):
        build({"A": (1, ["B"]), "B": (1, ["A"])})


def test_unknown_predecessor_is_detected():
    with pytest.raises(DataError):
        build({"A": (1, ["Z"])})


def test_self_dependency_is_detected():
    with pytest.raises(DataError):
        build({"A": (1, ["A"])})


def test_non_positive_duration_is_rejected():
    with pytest.raises(DataError):
        build({"A": (0, [])})


def test_demand_over_capacity_is_rejected(tmp_path):
    (tmp_path / "tasks.csv").write_text(
        "task_id,name,phase,duration,predecessors\nA,a,测试,3,\n", encoding="utf-8")
    (tmp_path / "resources.csv").write_text(
        "role_id,role_name,capacity\nR,角色,1\n", encoding="utf-8")
    (tmp_path / "task_resources.csv").write_text(
        "task_id,R\nA,2\n", encoding="utf-8")
    with pytest.raises(DataError):
        load_project(tmp_path)


def test_demand_matrix_shape_and_values():
    proj = build({"A": (3, []), "B": (2, ["A"])},
                 {"R": 2, "S": 1},
                 {"A": {"R": 2}, "B": {"S": 1}})
    roles, mat = proj.demand_matrix()
    assert roles == ["R", "S"]
    assert mat.shape == (2, 2)
    assert mat[proj.index["A"], 0] == 2
    assert mat[proj.index["B"], 1] == 1


def test_work_content_lower_bound():
    proj = build({"A": (4, [])}, {"R": 2}, {"A": {"R": 2}})
    lb = proj.work_content_lower_bound()
    assert lb["R"] == pytest.approx(4.0)   # 4 天 × 2 人 / 容量 2


def test_phase_summary_counts():
    proj = load_project("data")
    summary = proj.phase_summary()
    assert sum(v["count"] for v in summary.values()) == len(proj)
