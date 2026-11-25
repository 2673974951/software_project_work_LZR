"""项目数据模型与载入.

一个 RCPSP 实例由三张 CSV 描述：

tasks.csv
    任务表。列：task_id, name, phase, duration, predecessors。
    duration 为整数工作日；predecessors 用竖线分隔多个紧前任务，
    空表示起始任务。

resources.csv
    资源（研发角色）与容量。列：role_id, role_name, capacity。
    capacity 是指该角色在同一时刻最多可并行投入的人数。

task_resources.csv
    资源需求矩阵。列为 task_id 加各 role_id，单元格为该任务整段工期内
    持续占用该角色的人数（RCPSP 的标准假设：需求不随时间变化）。

设计说明
--------
RCPSP 的经典假设是"非抢占、工期固定、资源需求恒定"。本实现严格遵守这
三条：一旦任务开工就必须连续执行到结束，期间占用固定人数，因此资源曲
线可以用一个二维整数数组精确表示。
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np


class DataError(ValueError):
    """数据文件不合法时抛出。"""


@dataclass(frozen=True)
class Task:
    """单个任务（WBS 工作包）.

    Attributes:
        task_id: 任务编号，项目内唯一。
        name: 中文任务名称。
        phase: 所属阶段。
        duration: 工期（工作日），正整数。
        predecessors: 紧前任务编号元组。
    """

    task_id: str
    name: str
    phase: str
    duration: int
    predecessors: Tuple[str, ...] = ()


@dataclass(frozen=True)
class Resource:
    """可再生资源（研发角色）.

    Attributes:
        role_id: 角色编号，如 BE。
        role_name: 中文角色名。
        capacity: 同一时刻可并行投入的人数上限。
    """

    role_id: str
    role_name: str
    capacity: int


@dataclass
class Project:
    """RCPSP 实例：任务集合 + 依赖 DAG + 多资源约束.

    构造时即完成 DAG 校验与拓扑排序，之后 order / successors / index
    三个字段即为所有调度算法所依赖的规范顺序。
    """

    name: str
    tasks: Dict[str, Task]
    resources: Dict[str, Resource] = field(default_factory=dict)
    demands: Dict[str, Dict[str, int]] = field(default_factory=dict)

    order: List[str] = field(default_factory=list, init=False)
    successors: Dict[str, List[str]] = field(default_factory=dict, init=False)
    index: Dict[str, int] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        if not self.tasks:
            raise DataError("项目未包含任何任务")
        for tid, task in self.tasks.items():
            if task.duration <= 0:
                raise DataError("任务 %s 的工期必须为正整数" % tid)
            for p in task.predecessors:
                if p not in self.tasks:
                    raise DataError("任务 %s 的紧前任务 %s 不存在" % (tid, p))
                if p == tid:
                    raise DataError("任务 %s 依赖自身" % tid)
        # 先建逆邻接表：拓扑排序需要它计算入度
        self.successors = {tid: [] for tid in self.tasks}
        for tid, task in self.tasks.items():
            for p in task.predecessors:
                self.successors[p].append(tid)
        self.order = self._topological_order()
        self.index = {tid: i for i, tid in enumerate(self.order)}
        for tid in self.successors:
            self.successors[tid].sort(key=lambda t: self.index[t])

    def _topological_order(self) -> List[str]:
        """Kahn 拓扑排序；存在环时报错并给出残留任务集合。"""
        indeg = {tid: len(t.predecessors) for tid, t in self.tasks.items()}
        ready = sorted([tid for tid, d in indeg.items() if d == 0])
        order: List[str] = []
        while ready:
            tid = ready.pop(0)
            order.append(tid)
            for s in sorted(self.successors.get(tid, [])):
                indeg[s] -= 1
                if indeg[s] == 0:
                    ready.append(s)
            ready.sort()
        if len(order) != len(self.tasks):
            remaining = sorted(t for t in indeg if indeg[t] > 0)
            raise DataError("任务依赖关系存在环，涉及任务：%s" % remaining)
        return order

    # -------------------------------------------------------------- 便捷访问
    def __len__(self) -> int:
        return len(self.tasks)

    def task_list(self) -> List[Task]:
        return [self.tasks[t] for t in self.order]

    def durations(self) -> np.ndarray:
        """按拓扑序返回工期向量。"""
        return np.array([self.tasks[t].duration for t in self.order], dtype=float)

    def predecessor_indices(self) -> List[List[int]]:
        return [[self.index[p] for p in t.predecessors] for t in self.task_list()]

    def successor_indices(self) -> List[List[int]]:
        return [[self.index[s] for s in self.successors[t]] for t in self.order]

    def demand_matrix(self) -> Tuple[List[str], np.ndarray]:
        """返回 (角色编号列表, 需求矩阵)，矩阵形状 (任务数, 角色数)。"""
        roles = sorted(self.resources)
        mat = np.zeros((len(self.tasks), len(roles)), dtype=int)
        for i, tid in enumerate(self.order):
            for j, r in enumerate(roles):
                mat[i, j] = self.demands.get(tid, {}).get(r, 0)
        return roles, mat

    def capacities(self) -> np.ndarray:
        roles, _ = self.demand_matrix()
        return np.array([self.resources[r].capacity for r in roles], dtype=int)

    def total_work(self) -> Dict[str, int]:
        """各角色的总工作量（人日）。"""
        out = {r: 0 for r in self.resources}
        for task in self.tasks.values():
            for r, q in self.demands.get(task.task_id, {}).items():
                out[r] += q * task.duration
        return out

    def work_content_lower_bound(self) -> Dict[str, float]:
        """资源工作量下界：总工作量 / 容量.

        对任一角色 r，项目总工期不可能小于 sum( q_jr * d_j ) / b_r，
        因为该角色的总工作量必须在任意时刻不超过 b_r 的前提出清。
        """
        return {r: (w / self.resources[r].capacity if self.resources[r].capacity else 0.0)
                for r, w in self.total_work().items()}

    def phase_summary(self) -> Dict[str, Dict[str, float]]:
        """按阶段汇总任务数与工作量。"""
        out: Dict[str, Dict[str, float]] = {}
        for t in self.task_list():
            row = out.setdefault(t.phase, {"count": 0, "duration": 0.0, "effort": 0.0})
            row["count"] += 1
            row["duration"] += t.duration
            row["effort"] += t.duration * sum(self.demands.get(t.task_id, {}).values())
        return out


def _split(cell: str) -> Tuple[str, ...]:
    cell = (cell or "").strip()
    if not cell:
        return ()
    return tuple(p.strip() for p in cell.split("|") if p.strip())


def load_project(
    data_dir: "str | Path" = "data",
    name: str = "智慧校园综合服务平台 v2.0",
) -> Project:
    """从目录载入三张 CSV 并构建 Project.

    Raises:
        FileNotFoundError: 数据文件缺失。
        DataError: 任务/依赖/资源数据不合法（含需求量超容量）。
    """
    root = Path(data_dir)
    path = root / "tasks.csv"
    if not path.exists():
        raise FileNotFoundError("找不到任务表：%s" % path.resolve())

    tasks: Dict[str, Task] = {}
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            if not (row.get("task_id") or "").strip():
                continue
            tid = row["task_id"].strip()
            if tid in tasks:
                raise DataError("任务编号重复：%s" % tid)
            try:
                dur = int(float(row["duration"]))
            except (KeyError, TypeError, ValueError) as exc:
                raise DataError("任务 %s 的工期解析失败：%s" % (tid, exc)) from exc
            tasks[tid] = Task(
                task_id=tid,
                name=row.get("name", tid).strip(),
                phase=(row.get("phase") or "").strip() or "未分类",
                duration=dur,
                predecessors=_split(row.get("predecessors", "")),
            )

    resources: Dict[str, Resource] = {}
    rpath = root / "resources.csv"
    if rpath.exists():
        with rpath.open(encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                if not (row.get("role_id") or "").strip():
                    continue
                rid = row["role_id"].strip()
                cap = int(float(row["capacity"]))
                if cap <= 0:
                    raise DataError("角色 %s 的容量必须为正整数" % rid)
                resources[rid] = Resource(rid, row.get("role_name", rid).strip(), cap)

    demands: Dict[str, Dict[str, int]] = {tid: {} for tid in tasks}
    tpath = root / "task_resources.csv"
    if tpath.exists():
        with tpath.open(encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                tid = (row.get("task_id") or "").strip()
                if tid not in tasks:
                    raise DataError("task_resources.csv 中出现未知任务：%s" % tid)
                for key, val in row.items():
                    if key == "task_id" or not val or not str(val).strip():
                        continue
                    role = key.strip()
                    if role not in resources:
                        raise DataError("task_resources.csv 中出现未定义角色：%s" % role)
                    q = int(float(val))
                    if q < 0:
                        raise DataError("任务 %s 对角色 %s 的需求不能为负" % (tid, role))
                    if q > resources[role].capacity:
                        raise DataError(
                            "任务 %s 对角色 %s 的需求 %d 超过容量 %d，实例不可行"
                            % (tid, role, q, resources[role].capacity))
                    if q:
                        demands[tid][role] = q

    return Project(name=name, tasks=tasks, resources=resources, demands=demands)
