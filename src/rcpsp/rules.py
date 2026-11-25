"""启发式优先规则.

RCPSP 的启发式方法可以概括为"**优先规则 + 调度生成机制**"两部分。调度生
成机制（见 sgs.py）决定"怎么把优先序列变成一个可行调度"，优先规则则决定
"先排谁"。本模块实现 8 种经典规则，覆盖了"看关键路径""看时差""看工期"
"看网络结构""看资源占用"五个视角。

约定
----
所有规则的输出统一为"**数值越小越先排**"，便于在调度生成机制里用一次
argmin 完成选择。对于"越大越好"的规则（如后继数、秩位权），实现中已取
负号。

规则一览
--------
==========  ==========================================================
代码        含义
==========  ==========================================================
LFT         最晚完成时间优先（关键路径优先的经典实现）
LST         最晚开始时间优先
MSLK        最小时差优先（Most negative slack）
SPT         最短工期优先
MTS         最多后继任务优先（Most Total Successors，含传递后继）
GRPW        最大秩位权优先（自身工期 + 全部后继工期之和）
SRD         资源需求强度优先（占用资源比例之和最大者优先）
RANDOM      随机优先序列（对照基线，用于检验"规则是否真的有用"）
==========  ==========================================================

参考：
    Kolisch R. Serial and parallel resource-constrained project scheduling
    methods revisited: Theory and computation. EJOR, 1996, 90(2): 320-333.
    Valls V, Ballestin F, Quintanilla S. Justification and RCPSP: A technique
    that pays. EJOR, 2005, 165(2): 375-386.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np

from .cpm import compute_cpm
from .project import Project

__all__ = [
    "RuleSpec",
    "PRIORITY_RULES",
    "priority_values",
    "transitive_successor_count",
    "rank_positional_weight",
    "rule_table",
]


@dataclass(frozen=True)
class RuleSpec:
    """规则元数据，用于报告与命令行输出."""

    code: str
    name: str
    perspective: str
    note: str


PRIORITY_RULES: Dict[str, RuleSpec] = {
    "LFT": RuleSpec("LFT", "最晚完成时间优先", "关键路径",
                    "最晚完成时间最小者优先，是“关键路径优先”的经典实现"),
    "LST": RuleSpec("LST", "最晚开始时间优先", "关键路径",
                    "最晚开始时间最小者优先，与 LFT 同源但对长任务更敏感"),
    "MSLK": RuleSpec("MSLK", "最小时差优先", "时差",
                     "总时差最小者优先，最怕被推迟的任务先排"),
    "SPT": RuleSpec("SPT", "最短工期优先", "工期",
                    "工期最短者优先，希望快速清空任务、尽早释放资源"),
    "MTS": RuleSpec("MTS", "最多后继任务优先", "网络结构",
                    "传递后继数量最多者优先，优先解锁下游"),
    "GRPW": RuleSpec("GRPW", "最大秩位权优先", "网络结构",
                     "自身工期加全部后继工期之和最大者优先"),
    "SRD": RuleSpec("SRD", "资源需求强度优先", "资源占用",
                    "占用各资源容量比例之和最大者优先"),
    "RANDOM": RuleSpec("RANDOM", "随机优先序列", "对照基线",
                       "随机排序，用于检验启发式规则是否真的带来改进"),
}


def transitive_successor_count(project: Project) -> np.ndarray:
    """每个任务的**传递后继**数量（不含自身），按拓扑序返回.

    用逆拓扑序做一次动态规划即可，复杂度 O(n + m)。
    """
    n = len(project.order)
    succ_idx = project.successor_indices()
    depth = np.zeros(n, dtype=int)
    for i in range(n - 1, -1, -1):
        acc = 0
        for s in succ_idx[i]:
            acc += 1 + depth[s]
        depth[i] = acc
    return depth


def rank_positional_weight(project: Project, durations: np.ndarray) -> np.ndarray:
    """秩位权：自身工期 + 全部传递后继的工期之和.

    这是一个"路径长度"的宽松上界，用来近似该任务对最终工期的潜在影响。
    """
    n = len(project.order)
    succ_idx = project.successor_indices()
    w = np.zeros(n, dtype=float)
    for i in range(n - 1, -1, -1):
        w[i] = durations[i] + sum(w[s] for s in succ_idx[i])
    return w


def priority_values(
    project: Project,
    rule: str,
    durations: Optional[np.ndarray] = None,
    rng: Optional[np.random.Generator] = None,
) -> np.ndarray:
    """计算指定规则的优先级数组（**数值越小越先调度**）.

    Args:
        project: 项目对象。
        rule: PRIORITY_RULES 中的键。
        durations: 工期向量，缺省取项目工期。
        rng: 随机数生成器，仅 RANDOM 规则使用。

    Returns:
        形状 (n_tasks,) 的浮点数组，按拓扑序排列。
    """
    if rule not in PRIORITY_RULES:
        raise ValueError("未知的优先规则：%s（可选：%s）" % (rule, ", ".join(PRIORITY_RULES)))
    dur = project.durations() if durations is None else np.asarray(durations, dtype=float)
    n = len(project.order)

    if rule == "RANDOM":
        return (rng or np.random.default_rng(0)).random(n)

    cpm = compute_cpm(project, dur)
    if rule == "LFT":
        return cpm.lf.copy()
    if rule == "LST":
        return cpm.ls.copy()
    if rule == "MSLK":
        # 时差相同的情况下用 ES 打破平局，保证结果确定
        return cpm.total_float * 1e6 + cpm.es
    if rule == "SPT":
        return dur.copy()
    if rule == "MTS":
        return -transitive_successor_count(project).astype(float)
    if rule == "GRPW":
        return -rank_positional_weight(project, dur)
    if rule == "SRD":
        roles, mat = project.demand_matrix()
        caps = np.array([project.resources[r].capacity for r in roles], dtype=float)
        return -(mat / np.where(caps > 0, caps, 1.0)[None, :]).sum(axis=1)
    raise ValueError("规则 %s 尚未实现" % rule)


def rule_table() -> List[Dict[str, str]]:
    """返回规则清单，便于写报告表格。"""
    return [
        {"code": s.code, "name": s.name, "perspective": s.perspective, "note": s.note}
        for s in PRIORITY_RULES.values()
    ]
