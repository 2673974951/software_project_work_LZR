"""图表绘制.

统一的中文字体、配色与尺寸约定：图形默认 300 dpi PNG，横向宽度控制在
7.5 英寸以内，插入 A4 报告（正文宽 15.4cm）后文字约 8pt，可直接阅读。

视觉约定
--------
* 蓝色 = 实际占用 / 基准
* 红色 = 容量上限 / 冲突 / 增量
* 灰色 = 空闲 / 参照
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from .cpm import CPMResult, compute_cpm
from .project import Project
from .sgs import Schedule

__all__ = [
    "setup_style", "savefig", "plot_network", "plot_phase_workload",
    "plot_conflict", "plot_gantt", "plot_resource_profile",
    "plot_rule_comparison", "plot_utilisation", "plot_capacity",
    "plot_improvement",
]

FIG_DIR = Path("docs/figures")

MAIN = "#1f4e79"
ACCENT = "#c00000"
WARN = "#ed7d31"
SAFE = "#2e7d32"
GREY = "#8c8c8c"
PHASE_COLORS = {
    "启动": "#4472c4", "需求": "#5b9bd5", "设计": "#70ad47", "开发": "#ed7d31",
    "测试": "#a5a5a5", "上线": "#ffc000", "收尾": "#7030a0",
}


def setup_style() -> None:
    for family in ("Microsoft YaHei", "SimHei", "Noto Sans CJK SC",
                   "Source Han Sans SC", "PingFang SC"):
        try:
            matplotlib.font_manager.findfont(family, fallback_to_default=False)
            plt.rcParams["font.sans-serif"] = [family]
            break
        except Exception:
            continue
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["figure.dpi"] = 110
    plt.rcParams["savefig.dpi"] = 300
    plt.rcParams["savefig.bbox"] = "tight"
    plt.rcParams["font.size"] = 9
    plt.rcParams["axes.titlesize"] = 10
    plt.rcParams["axes.titleweight"] = "bold"
    plt.rcParams["axes.grid"] = True
    plt.rcParams["grid.alpha"] = 0.3
    plt.rcParams["axes.spines.top"] = False
    plt.rcParams["axes.spines.right"] = False


def savefig(fig, name: str, out_dir: "str | Path | None" = None) -> Path:
    d = Path(out_dir) if out_dir else FIG_DIR
    d.mkdir(parents=True, exist_ok=True)
    path = d / (name if name.endswith(".png") else name + ".png")
    fig.savefig(path)
    plt.close(fig)
    return path


# ------------------------------------------------------------------ 网络图
def _layers(project: Project) -> Dict[str, int]:
    layer: Dict[str, int] = {}
    for tid in project.order:
        preds = project.tasks[tid].predecessors
        layer[tid] = 0 if not preds else max(layer[p] for p in preds) + 1
    return layer


def plot_network(project: Project, cpm: Optional[CPMResult] = None, name: str = "fig02_network"):
    """分层 AON 网络图（竖向排布，适配 A4 纵向版面）.

    按"最长前驱链长度"给任务分层，同层任务横向排列，层从上到下推进；红色
    为关键路径上的任务与依赖边。竖向排布的好处是：A4 纵向版面（正文宽
    15.4cm）即可容纳 40 余个任务的网络图，无需横向页。
    """
    setup_style()
    cpm = cpm or compute_cpm(project)

    layer: Dict[str, int] = {}
    for tid in project.order:
        preds = project.tasks[tid].predecessors
        layer[tid] = 0 if not preds else max(layer[p] for p in preds) + 1

    by_layer: Dict[int, List[str]] = {}
    for tid in project.order:
        by_layer.setdefault(layer[tid], []).append(tid)
    pos: Dict[str, tuple] = {}
    for lv, tids in by_layer.items():
        tids.sort(key=lambda x: (cpm.es[cpm.task_ids.index(x)], x))
        for k, tid in enumerate(tids):
            pos[tid] = ((k - (len(tids) - 1) / 2.0), float(lv))

    n_layers = max(layer.values()) + 1
    max_nodes = max(len(v) for v in by_layer.values())
    # 画布尺寸按"插入 A4 后文字约 6pt"反推：正文宽 15.4cm ≈ 6.0 英寸
    W = max(5.2, max_nodes * 0.72)
    H = max(4.0, n_layers * 0.55)

    fig, ax = plt.subplots(figsize=(W, H))
    bw, bh = 0.86, 0.56
    crit_edges = set(zip(cpm.critical_path[:-1], cpm.critical_path[1:]))

    for tid, (x, y) in pos.items():
        task = project.tasks[tid]
        is_crit = tid in cpm.critical_tasks
        ax.add_patch(plt.Rectangle((x - bw / 2, y - bh / 2), bw, bh,
                                   facecolor="#fbe3e3" if is_crit else "#eef3fa",
                                   edgecolor=ACCENT if is_crit else MAIN,
                                   linewidth=1.4 if is_crit else 0.7, zorder=3))
        # 纵轴已反转，y 更小的位置在屏幕上方
        ax.text(x, y - 0.12, tid, ha="center", va="center", fontsize=6.6,
                fontweight="bold", color=ACCENT if is_crit else MAIN, zorder=4)
        label = task.name if len(task.name) <= 5 else task.name[:5]
        ax.text(x, y + 0.11, label, ha="center", va="center", fontsize=5.6, zorder=4)

    for tid in project.order:
        for s in project.successors[tid]:
            x1, y1 = pos[tid]
            x2, y2 = pos[s]
            is_crit = (tid, s) in crit_edges
            ax.annotate("", xy=(x2, y2 - bh / 2 - 0.02), xytext=(x1, y1 + bh / 2 + 0.02),
                        arrowprops=dict(arrowstyle="-|>", shrinkA=0, shrinkB=0,
                                        lw=1.7 if is_crit else 0.55, mutation_scale=7,
                                        color=ACCENT if is_crit else "#9aa7b8",
                                        alpha=1.0 if is_crit else 0.45,
                                        connectionstyle="arc3,rad=0.12"), zorder=2)

    ax.set_xlim(-max_nodes / 2.0 - 0.2, max_nodes / 2.0 + 0.2)
    ax.set_ylim(n_layers - 0.4, -0.6)
    ax.axis("off")
    ax.set_title("案例项目 AON 任务网络图（%d 个任务、%d 条依赖；红色为关键路径）"
                 % (len(project), sum(len(t.predecessors) for t in project.tasks.values())),
                 fontsize=9, pad=8)
    return savefig(fig, name)


def plot_phase_workload(project: Project, name: str = "fig01_phase"):
    """各阶段任务数与工作量."""
    setup_style()
    summary = project.phase_summary()
    phases = list(summary)
    effort = [summary[p]["effort"] for p in phases]
    counts = [summary[p]["count"] for p in phases]
    colors = [PHASE_COLORS.get(p, MAIN) for p in phases]

    fig, ax = plt.subplots(figsize=(7.2, 3.6))
    bars = ax.bar(phases, effort, color=colors, alpha=0.9)
    for b, e, c in zip(bars, effort, counts):
        ax.text(b.get_x() + b.get_width() / 2, e + 3, "%.0f 人日\n(%d 项)" % (e, c),
                ha="center", va="bottom", fontsize=7.5)
    ax.set_ylabel("工作量（人日）")
    ax.set_title("各阶段任务数与工作量分布")
    ax.set_ylim(0, max(effort) * 1.25)
    return savefig(fig, name)


# -------------------------------------------------------------- 资源冲突
def plot_conflict(project: Project, cpm: Optional[CPMResult] = None, name: str = "fig03_conflict"):
    """无资源约束计划下的资源冲突识别.

    对每个角色画一条"按依赖尽早开工时的逐日需求"曲线，并叠上容量红线；
    超出的部分用红色填充，直观说明 RCPSP 的必要性。
    """
    setup_style()
    cpm = cpm or compute_cpm(project)
    roles, mat = project.demand_matrix()
    T = int(round(cpm.project_duration))
    nrole = len(roles)
    ncol = 2
    nrow = int(np.ceil(nrole / ncol))

    fig, axes = plt.subplots(nrow, ncol, figsize=(7.6, 1.55 * nrow + 0.6), sharex=True)
    axes = np.atleast_2d(axes)
    t = np.arange(T)
    for k, role in enumerate(roles):
        ax = axes[k // ncol, k % ncol]
        prof = np.zeros(T, dtype=int)
        j = roles.index(role)
        for i, tid in enumerate(cpm.task_ids):
            prof[int(round(cpm.es[i])):int(round(cpm.ef[i]))] += mat[i, j]
        cap = project.resources[role].capacity
        ax.step(t, prof, where="post", color=MAIN, lw=1.2, label="需求")
        ax.axhline(cap, color=ACCENT, ls="--", lw=1.1)
        over = prof > cap
        if over.any():
            ax.fill_between(t, cap, prof, where=over, step="post",
                            color=ACCENT, alpha=0.35, label="冲突")
        ax.set_title("%s（%s，上限 %d）" % (role, project.resources[role].role_name, cap), fontsize=7.5)
        ax.set_ylim(0, max(cap, prof.max()) * 1.25 + 0.5)
        ax.tick_params(labelsize=6)
    for k in range(nrole, nrow * ncol):
        axes[k // ncol, k % ncol].axis("off")
    for ax in axes[-1]:
        ax.set_xlabel("时间（工作日）", fontsize=7)
    fig.suptitle("无资源约束计划下的资源需求与容量冲突（红色阴影为超载区间）",
                 fontsize=9, fontweight="bold")
    fig.tight_layout()
    return savefig(fig, name)


# ------------------------------------------------------------------ 甘特图
def plot_gantt(project: Project, schedule: Schedule, name: str = "fig05_gantt",
               title: str = ""):
    """资源受限调度甘特图（按阶段着色，标注关键序列）."""
    setup_style()
    from .metrics import critical_sequence
    crit = set(critical_sequence(project, schedule))
    n = len(schedule.task_ids)
    fig, ax = plt.subplots(figsize=(7.6, 9.0))
    for i, tid in enumerate(schedule.task_ids):
        task = project.tasks[tid]
        color = PHASE_COLORS.get(task.phase, MAIN)
        s, f = schedule.starts[i], schedule.finishes[i]
        ax.barh(i, f - s, left=s, height=0.66, color=color, alpha=0.92,
                edgecolor=ACCENT if tid in crit else "white",
                linewidth=1.4 if tid in crit else 0.4)
        ax.text(f + 0.6, i, "%s" % tid, va="center", fontsize=5.6, color=GREY)
    ax.set_yticks(range(n))
    ax.set_yticklabels(["%s %s" % (t, project.tasks[t].name[:9]) for t in schedule.task_ids],
                       fontsize=8.2)
    ax.invert_yaxis()
    ax.set_xlabel("时间（工作日）", fontsize=8, labelpad=6)
    ax.set_title(title or ("资源受限调度甘特图（%s，%s，总工期 %d 天；红框=资源受限关键序列）"
                           % (schedule.rule, schedule.method, schedule.makespan)),
                 fontsize=10.5)
    handles = [plt.Rectangle((0, 0), 1, 1, color=c) for c in PHASE_COLORS.values()]
    ax.legend(handles, list(PHASE_COLORS.keys()), fontsize=7.5, ncol=7,
              loc="upper center", bbox_to_anchor=(0.5, -0.07), frameon=False)
    ax.grid(axis="x", alpha=0.3)
    return savefig(fig, name)


def plot_resource_profile(project: Project, schedule: Schedule, name: str = "fig06_resource"):
    """调度方案下各角色的逐日占用与上限."""
    setup_style()
    roles = list(schedule.profiles)
    nrow = int(np.ceil(len(roles) / 2))
    fig, axes = plt.subplots(nrow, 2, figsize=(7.6, 1.35 * nrow + 0.7), sharex=True)
    axes = np.atleast_2d(axes)
    T = schedule.makespan
    t = np.arange(T)
    for k, role in enumerate(roles):
        ax = axes[k // 2, k % 2]
        prof = schedule.profiles[role][:T]
        cap = project.resources[role].capacity
        ax.fill_between(t, 0, prof, step="post", color=MAIN, alpha=0.55)
        ax.axhline(cap, color=ACCENT, ls="--", lw=1.0)
        ax.set_ylim(0, max(cap, prof.max() if prof.size else 1) * 1.25 + 0.4)
        ax.set_title("%s  利用率 %.0f%%" % (role, 100 * schedule.utilisation[role]), fontsize=9.5)
        ax.tick_params(labelsize=7.5)
    for k in range(len(roles), nrow * 2):
        axes[k // 2, k % 2].axis("off")
    for ax in axes[-1]:
        ax.set_xlabel("时间（工作日）", fontsize=7)
    fig.suptitle("调度方案下各角色逐日占用（蓝）与人力上限（红虚线）",
                 fontsize=9, fontweight="bold")
    fig.tight_layout()
    return savefig(fig, name)


# -------------------------------------------------------------- 规则对比
def plot_rule_comparison(rows: Sequence[dict], cpm_duration: float, name: str = "fig04_rules"):
    """各优先规则的工期对比（堆叠出"资源等待"部分）."""
    setup_style()
    labels = [r["rule"] for r in rows]
    total = np.array([r["makespan"] for r in rows], dtype=float)
    base = np.full_like(total, cpm_duration)

    fig, ax = plt.subplots(figsize=(7.4, 3.8))
    x = np.arange(len(labels))
    ax.bar(x, base, color="#c9d6e5", label="无资源约束工期（CPM）")
    ax.bar(x, total - base, bottom=base, color=WARN, alpha=0.9, label="资源冲突造成的等待")
    for xi, v in zip(x, total):
        ax.text(xi, v + 0.8, "%d" % v, ha="center", va="bottom", fontsize=7.5)
    best = int(np.argmin(total))
    ax.annotate("最优", (x[best], total[best]), textcoords="offset points",
                xytext=(0, 14), ha="center", fontsize=7, color=SAFE)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=8)
    ax.set_ylabel("项目总工期（工作日）")
    ax.set_ylim(0, total.max() * 1.18)
    ax.set_title("各优先规则的总工期对比（%.0f 天为无资源约束基准）" % cpm_duration)
    ax.legend(fontsize=7.5, loc="lower right")
    return savefig(fig, name)


def plot_utilisation(table: Sequence[dict], name: str = "fig07_utilisation"):
    """各角色资源利用率（含空闲率堆叠）."""
    setup_style()
    rows = sorted(table, key=lambda r: -float(r["utilisation"]))
    labels = ["%s\n%s" % (r["role_id"], str(r["role_name"])[:4]) for r in rows]
    busy = np.array([float(r["utilisation"]) for r in rows]) * 100
    idle = 100 - busy

    fig, ax = plt.subplots(figsize=(7.4, 3.6))
    x = np.arange(len(rows))
    ax.bar(x, busy, color=MAIN, alpha=0.9, label="忙碌")
    ax.bar(x, idle, bottom=busy, color="#d9d9d9", label="空闲")
    for xi, b in zip(x, busy):
        ax.text(xi, b / 2, "%.0f%%" % b, ha="center", va="center", fontsize=6.5, color="white")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=6.5)
    ax.set_ylabel("占容量比例（%）")
    ax.set_ylim(0, 108)
    ax.set_title("各角色资源利用率（按最优方案统计）")
    ax.legend(fontsize=7.5, ncol=2, loc="upper right")
    return savefig(fig, name)


def plot_capacity(rows: Sequence[dict], role: str, name: str = "fig08_capacity"):
    """某角色人数上限对总工期的影响（边际收益）."""
    setup_style()
    rules = sorted({r["rule"] for r in rows})
    fig, ax = plt.subplots(figsize=(7.4, 3.8))
    for rule in rules:
        sub = sorted([r for r in rows if r["rule"] == rule], key=lambda r: r["capacity"])
        if len(sub) < 2:
            continue
        ax.plot([r["capacity"] for r in sub], [r["makespan"] for r in sub],
                "o-", lw=1.5, ms=4, label=rule)
    ax.set_xlabel("%s 人数上限" % role)
    ax.set_ylabel("项目总工期（工作日）")
    ax.set_title("瓶颈角色（%s）人力投入的边际收益" % role)
    ax.legend(fontsize=7, title="优先规则", title_fontsize=7)
    return savefig(fig, name)


def plot_improvement(before: Sequence[dict], after: Sequence[dict], name: str = "fig09_improve"):
    """前向—后向改进前后各规则的工期对比."""
    setup_style()
    m1 = {r["rule"]: float(r["makespan"]) for r in before}
    m2 = {r["rule"]: float(r["makespan"]) for r in after}
    rules = [r["rule"] for r in before]
    x = np.arange(len(rules))
    a = [m1[r] for r in rules]
    b = [m2[r] for r in rules]

    fig, ax = plt.subplots(figsize=(7.4, 3.6))
    ax.bar(x - 0.2, a, width=0.4, color="#c9d6e5", label="改进前（串行 SGS）")
    ax.bar(x + 0.2, b, width=0.4, color=MAIN, label="改进后（+ 前向-后向）")
    for xi, av, bv in zip(x, a, b):
        if av != bv:
            ax.annotate("-%d" % (av - bv), (xi + 0.2, bv), textcoords="offset points",
                        xytext=(0, -12), ha="center", fontsize=6.5, color=ACCENT)
    ax.set_xticks(x)
    ax.set_xticklabels(rules, fontsize=7.5)
    ax.set_ylabel("项目总工期（工作日）")
    ax.set_ylim(min(b) * 0.9, max(a) * 1.06)
    ax.set_title("前向—后向改进（FBI）对各规则解的质量提升")
    ax.text(0.01, 0.02, "注：纵轴未从 0 开始，以突出规则间差异",
            transform=ax.transAxes, fontsize=6.5, color=GREY)
    ax.legend(fontsize=7.5, loc="lower right")
    return savefig(fig, name)
