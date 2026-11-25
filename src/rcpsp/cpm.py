"""关键路径法（CPM）：无资源约束下的理论最快工期.

作用有两个：

1. 作为 RCPSP 的**基准下界**——资源受限工期不可能短于 CPM 工期，二者之差
   就是"资源冲突造成的等待"；
2. 为 LFT / LST / MSLK 等优先规则提供时差信息，这些规则本质上都是
   "按 CPM 的紧迫程度排队"。

算法与教科书一致：正向传递求最早开始/完成，逆向传递求最晚开始/完成，
总时差为 0 的任务构成关键路径。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .project import Project

__all__ = ["CPMResult", "compute_cpm", "critical_path"]


@dataclass
class CPMResult:
    """一次确定性 CPM 计算的完整结果.

    Attributes:
        task_ids: 拓扑序任务编号。
        durations: 与 task_ids 对应的工期。
        es / ef / ls / lf: 最早开始、最早完成、最晚开始、最晚完成。
        total_float: 总时差 LS - ES。
        free_float: 自由时差。
        project_duration: 无资源约束下的项目总工期。
        critical_path: 一条连通的零时差任务链。
        critical_tasks: 全部零时差任务。
    """

    task_ids: List[str]
    durations: np.ndarray
    es: np.ndarray
    ef: np.ndarray
    ls: np.ndarray
    lf: np.ndarray
    total_float: np.ndarray
    free_float: np.ndarray
    project_duration: float
    critical_path: List[str] = field(default_factory=list)
    critical_tasks: List[str] = field(default_factory=list)

    def rows(self) -> List[Dict[str, object]]:
        out = []
        for i, tid in enumerate(self.task_ids):
            out.append({
                "task_id": tid,
                "duration": float(self.durations[i]),
                "ES": float(self.es[i]),
                "EF": float(self.ef[i]),
                "LS": float(self.ls[i]),
                "LF": float(self.lf[i]),
                "total_float": float(self.total_float[i]),
                "free_float": float(self.free_float[i]),
                "critical": bool(self.total_float[i] <= 1e-9),
            })
        return out


def critical_path(
    task_ids: Sequence[str],
    durations: np.ndarray,
    pred_idx: Sequence[Sequence[int]],
    succ_idx: Sequence[Sequence[int]],
    ef: np.ndarray,
    total_float: np.ndarray,
    tol: float = 1e-9,
) -> List[str]:
    """从 CPM 结果中抽取一条首尾相连的关键链.

    只取"零时差且 ES 正好等于前驱 EF"的后继，保证输出的路径在网络上
    是连通的；若同时存在多个候选，选择工期最长者。
    """
    zero = [i for i in range(len(task_ids)) if total_float[i] <= tol]
    if not zero:
        return []
    starts = [i for i in zero if not pred_idx[i]] or zero
    cur = max(starts, key=lambda i: float(ef[i]))
    path = [task_ids[cur]]
    guard = 0
    while guard < len(task_ids):
        guard += 1
        cands = [
            s for s in succ_idx[cur]
            if total_float[s] <= tol
            and abs((float(ef[s]) - float(durations[s])) - float(ef[cur])) <= 1e-9
        ]
        if not cands:
            break
        cur = max(cands, key=lambda s: float(durations[s]))
        path.append(task_ids[cur])
    return path


def compute_cpm(
    project: Optional[Project] = None,
    durations: Optional[np.ndarray] = None,
    *,
    task_ids: Optional[Sequence[str]] = None,
    pred_idx: Optional[Sequence[Sequence[int]]] = None,
    succ_idx: Optional[Sequence[Sequence[int]]] = None,
    horizon: Optional[float] = None,
    tol: float = 1e-9,
) -> CPMResult:
    """计算确定性 CPM.

    Args:
        project: 项目对象（给出时可自动推导结构）。
        durations: 工期向量；缺省取项目本身的工期。
        task_ids / pred_idx / succ_idx: 显式指定结构（与 project 二选一）。
        horizon: 逆向传递的边界；缺省取正向传递得到的最大 EF。
        tol: 判零容差。
    """
    if project is not None:
        task_ids = list(project.order)
        pred_idx = project.predecessor_indices()
        succ_idx = project.successor_indices()
        if durations is None:
            durations = project.durations()
    assert task_ids is not None and pred_idx is not None and succ_idx is not None
    dur = np.asarray(durations, dtype=float).reshape(-1)
    if dur.shape[0] != len(task_ids):
        raise ValueError("工期向量长度与任务数量不一致")

    n = len(task_ids)
    es = np.zeros(n)
    ef = np.zeros(n)
    for i, preds in enumerate(pred_idx):
        es[i] = max((ef[p] for p in preds), default=0.0)
        ef[i] = es[i] + dur[i]
    T = float(ef.max()) if n else 0.0
    bound = T if horizon is None else float(horizon)

    ls = np.zeros(n)
    lf = np.zeros(n)
    for i in range(n - 1, -1, -1):
        succs = succ_idx[i]
        lf[i] = min((ls[s] for s in succs), default=bound)
        ls[i] = lf[i] - dur[i]

    tf = np.where(np.abs(ls - es) < tol, 0.0, ls - es)
    ff = np.zeros(n)
    for i in range(n):
        succs = succ_idx[i]
        ff[i] = (min(es[s] for s in succs) if succs else T) - ef[i]
    ff = np.where(np.abs(ff) < tol, 0.0, ff)

    path = critical_path(task_ids, dur, pred_idx, succ_idx, ef, tf, tol)
    return CPMResult(
        task_ids=list(task_ids), durations=dur, es=es, ef=ef, ls=ls, lf=lf,
        total_float=tf, free_float=ff, project_duration=T,
        critical_path=path,
        critical_tasks=[task_ids[i] for i in range(n) if tf[i] <= tol],
    )
