"""调度生成机制：串行 SGS、并行 SGS 与前向—后向改进.

一、为什么要"生成机制"
-----------------------
优先规则只给出一个"谁先谁后"的排序，但排序本身不是调度：还得决定每个
任务**具体放在第几天开工**。这个"把优先序列翻译成可行调度"的过程叫调度
生成机制（Schedule Generation Scheme, SGS）。同一套优先序列配合不同的
SGS，可能得到不同的工期。经典的有两种：

**串行 SGS（Serial SGS）**
    按优先顺序逐个取任务，把它放在"满足紧前关系、且整段工期内资源都不
    超限"的最早时刻。生成的调度一定是**活动调度**（active schedule，不
    存在可以整体前移而不违反约束的任务），而活动调度的解空间包含最优解。

**并行 SGS（Parallel SGS）**
    时间逐日推进，在每个时刻把所有"紧前已完成且当前资源够用"的任务按优
    先级依次开工。生成的调度一定是**非延迟调度**（non-delay schedule，
    不存在"资源闲着却有无任务可开工"的情形）——没有任务可等）。非延迟
    调度集合是活动调度集合的子集，因此并行 SGS 可能错过某些更好的活动
    调度，这一点会在实验中被观察到。

二、前向—后向改进（Forward-Backward Improvement）
-------------------------------------------------
Valls 等（2005）提出的"双重排序"（double justification）是一种廉价而
有效的局部改进：

1. **右对齐（backward）**：保持当前总工期 T 不变，按开工时间**从晚到早**
   依次把每个任务尽量往后放；
2. **左对齐（forward）**：再按新的开工时间**从早到晚**，用串行 SGS 把每
   个任务尽量往前放。

一次"右对齐 + 左对齐"称为一轮。由于左对齐的目标是让每个任务尽早开工，
总工期不会变长；若能变短就接受新解，否则停止。这个过程不增加任何评价
函数的计算量，却常常能把启发式解压缩 2%~8%。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .project import Project
from .rules import PRIORITY_RULES, priority_values

__all__ = ["Schedule", "serial_sgs", "parallel_sgs", "double_justify",
           "schedule_with_rule", "random_multistart"]


@dataclass
class Schedule:
    """一个可行调度方案.

    Attributes:
        task_ids: 拓扑序任务编号。
        starts / finishes: 开工与完工时间（整数工作日）。
        makespan: 项目总工期。
        rule: 产生该方案的规则名称。
        order: 实际调度顺序（任务编号列表）。
        profiles: 角色 -> 逐日资源占用曲线（长度 = makespan）。
        utilisation: 角色 -> 平均资源利用率（0~1）。
        role_names: 角色 -> 中文名。
        method: 生成机制名称（串行 SGS / 并行 SGS / 前向-后向改进后）。
    """

    task_ids: List[str]
    starts: np.ndarray
    finishes: np.ndarray
    makespan: int
    rule: str
    order: List[str] = field(default_factory=list)
    profiles: Dict[str, np.ndarray] = field(default_factory=dict)
    utilisation: Dict[str, float] = field(default_factory=dict)
    role_names: Dict[str, str] = field(default_factory=dict)
    method: str = "Serial SGS"

    def to_dict(self) -> Dict[str, float]:
        """任务编号 -> 开工时间。"""
        return {tid: float(self.starts[i]) for i, tid in enumerate(self.task_ids)}

    def rows(self) -> List[Dict[str, object]]:
        return [
            {"task_id": tid,
             "start": int(self.starts[i]),
             "finish": int(self.finishes[i]),
             "duration": int(self.finishes[i] - self.starts[i])}
            for i, tid in enumerate(self.task_ids)
        ]

    def active_tasks(self) -> List[str]:
        """参与调度的任务数（本项目全部任务都必须调度）。"""
        return list(self.task_ids)


def _build_profiles(
    project: Project, starts: np.ndarray, finishes: np.ndarray, makespan: int
) -> Tuple[Dict[str, np.ndarray], Dict[str, float], Dict[str, str]]:
    """由开工/完工时间重建资源曲线，并计算各角色平均利用率."""
    roles, mat = project.demand_matrix()
    profiles: Dict[str, np.ndarray] = {}
    util: Dict[str, float] = {}
    names: Dict[str, str] = {}
    for j, r in enumerate(roles):
        prof = np.zeros(makespan, dtype=float)
        for i in range(len(starts)):
            prof[int(starts[i]):int(finishes[i])] += mat[i, j]
        cap = project.resources[r].capacity
        profiles[r] = prof
        names[r] = project.resources[r].role_name
        util[r] = float(prof.sum() / (cap * makespan)) if makespan else 0.0
    return profiles, util, names


def serial_sgs(
    project: Project,
    durations: Optional[np.ndarray] = None,
    rule: str = "LFT",
    rng: Optional[np.random.Generator] = None,
    priority_override: Optional[np.ndarray] = None,
) -> Schedule:
    """串行调度生成机制.

    Args:
        project: 项目对象。
        durations: 工期向量，缺省取项目工期。
        rule: PRIORITY_RULES 中的键（priority_override 给出时仅作标识）。
        rng: 随机数生成器，仅 RANDOM 规则使用。
        priority_override: 直接指定优先级（越小越优先）。

    Returns:
        Schedule —— 一定是可行且活动（active）的调度。
    """
    n = len(project.order)
    dur = project.durations() if durations is None else np.asarray(durations, dtype=float)
    if dur.shape[0] != n:
        raise ValueError("工期向量长度与任务数量不一致")
    dur = np.maximum(np.rint(dur).astype(int), 1)

    prio = (np.asarray(priority_override, dtype=float)
            if priority_override is not None
            else priority_values(project, rule, dur, rng))

    roles, mat = project.demand_matrix()
    caps = np.array([project.resources[r].capacity for r in roles], dtype=int)
    horizon = int(dur.sum()) + 1
    profile = np.zeros((len(roles), horizon), dtype=int)

    pred_idx = project.predecessor_indices()
    succ_idx = project.successor_indices()

    starts = np.full(n, -1, dtype=int)
    finishes = np.full(n, -1, dtype=int)
    scheduled = np.zeros(n, dtype=bool)
    remaining = np.array([len(p) for p in pred_idx], dtype=int)
    order: List[str] = []

    for _ in range(n):
        eligible = [i for i in range(n) if not scheduled[i] and remaining[i] == 0]
        if not eligible:
            raise RuntimeError("调度陷入死锁：请检查依赖关系是否存在环")
        chosen = min(eligible, key=lambda i: (prio[i], i))

        t0 = max((int(finishes[p]) for p in pred_idx[chosen]), default=0)
        need = mat[chosen]
        d = int(dur[chosen])
        if need.any():
            t = t0
            while t + d <= horizon:
                if np.all(profile[:, t:t + d] + need[:, None] <= caps[:, None]):
                    break
                t += 1
            else:
                raise RuntimeError("资源曲线溢出，horizon 估计不足")
            start = t
        else:
            start = t0

        starts[chosen] = start
        finishes[chosen] = start + d
        scheduled[chosen] = True
        order.append(project.order[chosen])
        if need.any():
            profile[:, start:start + d] += need[:, None]
        for s in succ_idx[chosen]:
            remaining[s] -= 1

    makespan = int(finishes.max()) if n else 0
    profiles, util, names = _build_profiles(project, starts, finishes, makespan)
    return Schedule(
        task_ids=list(project.order), starts=starts, finishes=finishes,
        makespan=makespan, rule=rule, order=order,
        profiles=profiles, utilisation=util, role_names=names,
        method="Serial SGS",
    )


def parallel_sgs(
    project: Project,
    durations: Optional[np.ndarray] = None,
    rule: str = "LFT",
    rng: Optional[np.random.Generator] = None,
    priority_override: Optional[np.ndarray] = None,
) -> Schedule:
    """并行调度生成机制（时间推进式）.

    在每个整数时刻 t，把"紧前任务全部完成、且当前资源余量足够"的任务按
    优先级依次开工。生成的是**非延迟调度**，其解空间是活动调度的子集，
    因此工期通常不优于串行 SGS——这一结论会在报告中被实验验证。

    Args:
        同 serial_sgs。

    Returns:
        Schedule
    """
    n = len(project.order)
    dur = project.durations() if durations is None else np.asarray(durations, dtype=float)
    if dur.shape[0] != n:
        raise ValueError("工期向量长度与任务数量不一致")
    dur = np.maximum(np.rint(dur).astype(int), 1)

    prio = (np.asarray(priority_override, dtype=float)
            if priority_override is not None
            else priority_values(project, rule, dur, rng))

    roles, mat = project.demand_matrix()
    caps = np.array([project.resources[r].capacity for r in roles], dtype=int)
    pred_idx = project.predecessor_indices()
    succ_idx = project.successor_indices()
    horizon = int(dur.sum()) + 1

    starts = np.full(n, -1, dtype=int)
    finishes = np.full(n, -1, dtype=int)
    scheduled = np.zeros(n, dtype=bool)
    order: List[str] = []

    t = 0
    guard = 0
    while not scheduled.all():
        guard += 1
        if guard > horizon * 2:
            raise RuntimeError("并行 SGS 未能推进，请检查数据")

        # 当前时刻正在执行的任务所占用的资源
        running = [(i, int(finishes[i])) for i in range(n) if scheduled[i] and finishes[i] > t]
        used = np.zeros(len(roles), dtype=int)
        for i, _ in running:
            used += mat[i]

        eligible = [i for i in range(n)
                    if not scheduled[i]
                    and all(scheduled[p] and finishes[p] <= t for p in pred_idx[i])]
        eligible.sort(key=lambda i: (prio[i], i))
        for i in eligible:
            need = mat[i]
            if np.all(used + need <= caps):
                starts[i] = t
                finishes[i] = t + int(dur[i])
                scheduled[i] = True
                used = used + need
                order.append(project.order[i])

        t += 1

    makespan = int(finishes.max()) if n else 0
    profiles, util, names = _build_profiles(project, starts, finishes, makespan)
    return Schedule(
        task_ids=list(project.order), starts=starts, finishes=finishes,
        makespan=makespan, rule=rule, order=order,
        profiles=profiles, utilisation=util, role_names=names,
        method="Parallel SGS",
    )


def _latest_feasible(
    profile: np.ndarray, need: np.ndarray, caps: np.ndarray, latest_start: int, d: int
) -> Optional[int]:
    """从 latest_start 向下找最近的、整段 [t, t+d) 资源可行的开工时刻。

    返回 None 表示在 [0, latest_start] 区间内没有可行位置（这在"被其他任务
    抢占资源"时可能发生），调用方需要退化处理。
    """
    t = latest_start
    while t >= 0:
        if np.all(profile[:, t:t + d] + need[:, None] <= caps[:, None]):
            return t
        t -= 1
    return None


def _earliest_feasible(
    profile: np.ndarray, need: np.ndarray, caps: np.ndarray, earliest: int, d: int
) -> int:
    """从 earliest 向上找最早的、整段 [t, t+d) 资源可行的开工时刻。"""
    t = earliest
    horizon = profile.shape[1]
    while t + d <= horizon:
        if np.all(profile[:, t:t + d] + need[:, None] <= caps[:, None]):
            return t
        t += 1
    raise RuntimeError("左对齐失败：资源曲线溢出")


def double_justify(
    project: Project,
    schedule: Schedule,
    max_rounds: int = 50,
    durations: Optional[np.ndarray] = None,
) -> Tuple[Schedule, List[int]]:
    """前向—后向改进（double justification）.

    流程：

    1. 以当前调度中各任务的开工时间为序，做一次**右对齐**：保持总工期
       不变，按开工时间从晚到早把任务尽量往后推；
    2. 再用右对齐后的开工时间为序，做一次**左对齐**（等价于串行 SGS）；
    3. 若工期变短则接受并重复，否则停止。

    Args:
        project: 项目对象。
        schedule: 初始可行调度。
        max_rounds: 最大迭代轮数。
        durations: 工期向量，缺省取项目工期。

    Returns:
        (改进后的 Schedule, 每轮工期历史)。
    """
    n = len(project.order)
    dur = project.durations() if durations is None else np.asarray(durations, dtype=float)
    dur = np.maximum(np.rint(dur).astype(int), 1)
    roles, mat = project.demand_matrix()
    caps = np.array([project.resources[r].capacity for r in roles], dtype=int)
    pred_idx = project.predecessor_indices()
    succ_idx = project.successor_indices()

    current = schedule
    history = [current.makespan]
    best = schedule

    for _ in range(max_rounds):
        # ---------- 右对齐 ----------
        # T+1 列足以容纳 [0, T] 区间的所有合法位置
        T = current.makespan
        profile = np.zeros((len(roles), T + 1), dtype=int)
        new_start = np.full(n, -1, dtype=int)
        new_finish = np.full(n, -1, dtype=int)
        placed = np.zeros(n, dtype=bool)
        seq = sorted(range(n), key=lambda i: int(current.starts[i]), reverse=True)

        for i in seq:
            latest_finish = T
            for s in succ_idx[i]:
                if placed[s]:
                    latest_finish = min(latest_finish, int(new_start[s]))
            latest_start = max(latest_finish - int(dur[i]), 0)
            t = _latest_feasible(profile, mat[i], caps, latest_start, int(dur[i]))
            if t is None:
                # 退化：从最早可行位置放置，保证右对齐一定成功
                t = _earliest_feasible(profile, mat[i], caps, 0, int(dur[i]))
            new_start[i] = t
            new_finish[i] = t + int(dur[i])
            placed[i] = True
            if mat[i].any():
                profile[:, t:t + int(dur[i])] += mat[i][:, None]

        # ---------- 左对齐（串行 SGS） ----------
        order_idx = np.empty(n, dtype=int)
        for rank, i in enumerate(sorted(range(n), key=lambda i: int(new_start[i]))):
            order_idx[i] = rank
        try:
            nxt = serial_sgs(project, dur, rule=current.rule,
                             priority_override=order_idx.astype(float))
        except RuntimeError:
            break
        history.append(nxt.makespan)

        if nxt.makespan < current.makespan:
            current = nxt
            if nxt.makespan < best.makespan:
                best = nxt
        else:
            break

    best.method = "Serial SGS + 前向-后向改进"
    return best, history


def schedule_with_rule(
    project: Project,
    rule: str = "LFT",
    durations: Optional[np.ndarray] = None,
    seed: int = 0,
    method: str = "serial",
) -> Schedule:
    """便捷封装：指定规则与生成机制排一次.

    Args:
        project: 项目对象。
        rule: PRIORITY_RULES 中的键。
        durations: 工期向量。
        seed: RANDOM 规则的随机种子。
        method: "serial" 或 "parallel"。
    """
    dur = project.durations() if durations is None else np.asarray(durations, dtype=float)
    sgs = serial_sgs if method == "serial" else parallel_sgs
    return sgs(project, dur, rule=rule, rng=np.random.default_rng(seed))


def random_multistart(
    project: Project,
    n_samples: int = 500,
    seed: int = 42,
    durations: Optional[np.ndarray] = None,
    improve: bool = True,
) -> Tuple[Schedule, List[int]]:
    """随机键多起点搜索：作为"已知最好解"的参照基准.

    RCPSP 的最优解在本实例规模上无法严格求出，因此用一个廉价的随机搜索
    给出"可达下界"：随机生成 n_samples 条优先序列，各自用串行 SGS 排一
    次（可选叠加前向—后向改进），保留总工期最短的一条。

    它的用途是评价启发式规则：如果某条规则的结果与随机搜索最好解只差
    1%~2%，说明规则已经充分挖掘了实例结构；如果差得很多，则说明规则在
    该实例上失效。

    Args:
        project: 项目对象。
        n_samples: 随机样本数。
        seed: 随机种子。
        durations: 工期向量。
        improve: 是否对每个样本叠加前向—后向改进（更慢但更好）。

    Returns:
        (最好的 Schedule, 每代最好解的历史列表)
    """
    dur = project.durations() if durations is None else np.asarray(durations, dtype=float)
    rng = np.random.default_rng(seed)
    n = len(project.order)
    best: Optional[Schedule] = None
    history: List[int] = []
    for k in range(n_samples):
        prio = rng.random(n)
        sch = serial_sgs(project, dur, rule="RANDOM", priority_override=prio)
        if improve:
            sch, _ = double_justify(project, sch, durations=dur)
        if best is None or sch.makespan < best.makespan:
            best = sch
        history.append(best.makespan)
    assert best is not None
    best.rule = "MULTISTART"
    best.method = "随机键多起点" + (" + 前向-后向改进" if improve else "")
    return best, history
