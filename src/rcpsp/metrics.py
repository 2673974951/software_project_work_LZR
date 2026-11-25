"""调度方案的度量指标：工期、资源利用率、冲突强度、下界.

本模块回答报告里必须出现的几类数据：

* **资源利用率统计表**——每个角色的工作量、占用容量与利用率；
* **资源冲突识别**——在"按依赖尽早开工"的无约束计划中有多少天、哪个
  角色出现了超载，这是 RCPSP 存在的根本原因；
* **方案对比**——多种优先规则下的总工期、增量、利用率、平滑度；
* **下界**——判断启发式解离最优还有多远。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

import numpy as np

from .cpm import CPMResult, compute_cpm
from .project import Project
from .sgs import Schedule, serial_sgs

__all__ = [
    "resource_utilisation_table",
    "phase_utilisation",
    "resource_conflict_report",
    "critical_sequence",
    "schedule_metrics",
    "compare_rules",
    "lower_bounds",
]


def resource_utilisation_table(project: Project, schedule: Schedule) -> List[Dict[str, object]]:
    """资源利用率统计表（报告必备）.

    Returns:
        每个角色一行：容量、承担任务数、忙碌人日、容量人日、利用率、
        峰值占用、平均占用。
    """
    rows: List[Dict[str, object]] = []
    for role in sorted(project.resources):
        cap = project.resources[role].capacity
        prof = schedule.profiles[role]
        busy = float(prof.sum())
        avail = float(cap * schedule.makespan)
        n_tasks = sum(1 for t in project.order if project.demands.get(t, {}).get(role, 0))
        rows.append({
            "role_id": role,
            "role_name": project.resources[role].role_name,
            "capacity": cap,
            "task_count": n_tasks,
            "busy_person_days": round(busy, 2),
            "capacity_person_days": round(avail, 2),
            "utilisation": round(busy / avail, 4) if avail else 0.0,
            "peak_usage": int(prof.max()) if prof.size else 0,
            "mean_usage": round(float(prof.mean()), 3) if prof.size else 0.0,
            "idle_days": round(float((cap - prof).sum()), 2),
        })
    return rows


def phase_utilisation(project: Project, schedule: Schedule) -> List[Dict[str, object]]:
    """分阶段的资源投入统计，用于说明"忙闲不均"."""
    idx = {t: i for i, t in enumerate(schedule.task_ids)}
    buckets: Dict[str, Dict[str, float]] = {}
    for task in project.task_list():
        i = idx[task.task_id]
        b = buckets.setdefault(task.phase, {"tasks": 0, "span": 0.0, "effort": 0.0})
        b["tasks"] += 1
        b["span"] = max(b["span"], float(schedule.finishes[i])) - min(
            b.get("start", 1e9), float(schedule.starts[i]))
        b["start"] = min(b.get("start", 1e9), float(schedule.starts[i]))
        b["effort"] += task.duration * sum(project.demands.get(task.task_id, {}).values())
    out = []
    for phase, b in buckets.items():
        out.append({
            "phase": phase,
            "task_count": int(b["tasks"]),
            "window_days": round(float(b["span"]), 1),
            "effort_person_days": round(float(b["effort"]), 1),
        })
    return out


def resource_conflict_report(project: Project, cpm: Optional[CPMResult] = None) -> List[Dict[str, object]]:
    """识别"无资源约束计划"中的资源冲突.

    做法：按 CPM 的最早开始时间排出一份"理想进度"，逐日累加各角色的需求，
    与容量比较。凡是需求大于容量的日期即为冲突日。

    Returns:
        每角色一行：峰值需求、容量、冲突天数、最大超载人数、冲突强度。
    """
    cpm = cpm or compute_cpm(project)
    idx = {t: i for i, t in enumerate(cpm.task_ids)}
    roles, mat = project.demand_matrix()
    T = int(round(cpm.project_duration))
    rows: List[Dict[str, object]] = []
    for j, role in enumerate(roles):
        prof = np.zeros(max(T, 1), dtype=int)
        for i, tid in enumerate(cpm.task_ids):
            s = int(round(cpm.es[i]))
            f = int(round(cpm.ef[i]))
            prof[s:f] += mat[i, j]
        cap = project.resources[role].capacity
        overload = prof - cap
        conflict_days = int((overload > 0).sum())
        peak = int(prof.max()) if prof.size else 0
        rows.append({
            "role_id": role,
            "role_name": project.resources[role].role_name,
            "capacity": cap,
            "peak_demand": peak,
            "conflict_days": conflict_days,
            "max_overload": int(max(overload.max(), 0)) if prof.size else 0,
            "conflict_ratio": round(conflict_days / T, 4) if T else 0.0,
            "intensity": round(peak / cap, 3) if cap else 0.0,
            "has_conflict": bool(conflict_days > 0),
            "_profile": prof,  # 供绘图使用，输出 CSV 前应剔除
        })
    return rows


def critical_sequence(project: Project, schedule: Schedule) -> List[str]:
    """资源受限调度下的"关键序列".

    与 CPM 的关键路径不同，RCPSP 的关键任务由**资源约束**决定：某个任务
    只要推迟一天，总工期就推迟一天。计算方式是固定总工期做一次逆向传递，
    找出 LS == ES 的任务。

    Returns:
        零时差任务编号列表（按拓扑序）。
    """
    task_ids = schedule.task_ids
    n = len(task_ids)
    idx = {t: i for i, t in enumerate(task_ids)}
    succ = project.successor_indices()
    T = schedule.makespan
    ls = np.zeros(n)
    lf = np.zeros(n)
    for i in range(n - 1, -1, -1):
        lf[i] = min((ls[s] for s in succ[i]), default=T)
        ls[i] = lf[i] - (int(schedule.finishes[i]) - int(schedule.starts[i]))
    return [task_ids[i] for i in range(n)
            if abs(ls[i] - float(schedule.starts[i])) < 1e-9]


def schedule_metrics(project: Project, schedule: Schedule, cpm: Optional[CPMResult] = None) -> Dict[str, object]:
    """把单个方案的关键指标打包成一个字典."""
    cpm = cpm or compute_cpm(project)
    utils = np.array(list(schedule.utilisation.values()), dtype=float)
    # 平滑度：逐日总占用的标准差越小，资源曲线越平稳
    total = np.zeros(schedule.makespan)
    for role in schedule.profiles:
        total += schedule.profiles[role]
    crit = critical_sequence(project, schedule)
    return {
        "rule": schedule.rule,
        "method": schedule.method,
        "makespan": float(schedule.makespan),
        "resource_delay": float(schedule.makespan) - cpm.project_duration,
        "resource_delay_ratio": ((float(schedule.makespan) - cpm.project_duration)
                                 / cpm.project_duration if cpm.project_duration else 0.0),
        "mean_utilisation": float(utils.mean()) if utils.size else 0.0,
        "max_utilisation": float(utils.max()) if utils.size else 0.0,
        "min_utilisation": float(utils.min()) if utils.size else 0.0,
        "profile_std": float(total.std()),
        "critical_task_count": len(crit),
        "critical_sequence": crit,
    }


def compare_rules(
    project: Project,
    rules: Sequence[str],
    durations: Optional[np.ndarray] = None,
    method: str = "serial",
    improve: bool = False,
    seed: int = 0,
) -> List[Dict[str, object]]:
    """在同一实例上运行多种优先规则并对比.

    Args:
        project: 项目对象。
        rules: 规则代码序列。
        durations: 工期向量。
        method: "serial" 或 "parallel"。
        improve: 是否叠加前向—后向改进。
        seed: RANDOM 规则种子。

    Returns:
        每规则一行，按总工期升序排列；"schedule" 字段保存完整方案对象。
    """
    from .sgs import double_justify, parallel_sgs

    dur = project.durations() if durations is None else np.asarray(durations, dtype=float)
    cpm = compute_cpm(project, dur)
    base = cpm.project_duration
    rows: List[Dict[str, object]] = []
    for rule in rules:
        sgs = serial_sgs if method == "serial" else parallel_sgs
        sch = sgs(project, dur, rule=rule, rng=np.random.default_rng(seed))
        if improve:
            sch, _ = double_justify(project, sch, durations=dur)
        utils = np.array(list(sch.utilisation.values()), dtype=float)
        total = np.zeros(sch.makespan)
        for role in sch.profiles:
            total += sch.profiles[role]
        rows.append({
            "rule": rule,
            "method": sch.method,
            "makespan": float(sch.makespan),
            "delta_days": float(sch.makespan) - base,
            "delta_ratio": (float(sch.makespan) - base) / base if base else 0.0,
            "mean_utilisation": float(utils.mean()) if utils.size else 0.0,
            "max_utilisation": float(utils.max()) if utils.size else 0.0,
            "min_utilisation": float(utils.min()) if utils.size else 0.0,
            "profile_std": float(total.std()),
            "critical_task_count": len(critical_sequence(project, sch)),
            "schedule": sch,
        })
    rows.sort(key=lambda r: (r["makespan"], r["rule"]))
    return rows


def lower_bounds(project: Project, cpm: Optional[CPMResult] = None) -> Dict[str, float]:
    """计算 RCPSP 的若干下界，用于评估启发式解的质量.

    * CPM 工期：无资源约束的最短可能工期；
    * 工作量下界：对每个角色，总工作量 / 容量；
    * 综合下界：二者取大。

    Returns:
        含各下界与所选下界的字典。
    """
    cpm = cpm or compute_cpm(project)
    work = project.work_content_lower_bound()
    work_max = max(work.values()) if work else 0.0
    return {
        "cpm": float(cpm.project_duration),
        "work_content": float(work_max),
        "lb": float(max(cpm.project_duration, work_max)),
        "binding_resource": max(work, key=work.get) if work else "",
    }
