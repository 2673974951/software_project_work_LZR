"""一键复现全部实验数据与论文图表.

用法::

    python scripts/run_experiment.py                 # 默认参数
    python scripts/run_experiment.py --samples 1000  # 加大随机搜索力度
    python scripts/run_experiment.py --no-search     # 跳过随机多起点搜索

输出：

* outputs/*.csv       各类结果表（可直接贴进报告）
* outputs/summary.md  关键结论摘要
* docs/figures/*.png  全部图表（300 dpi）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rcpsp import plotting  # noqa: E402
from rcpsp.cpm import compute_cpm  # noqa: E402
from rcpsp.metrics import (  # noqa: E402
    compare_rules,
    lower_bounds,
    phase_utilisation,
    resource_conflict_report,
    resource_utilisation_table,
)
from rcpsp.project import Resource, load_project  # noqa: E402
from rcpsp.rules import PRIORITY_RULES, rule_table  # noqa: E402
from rcpsp.sgs import double_justify, random_multistart, schedule_with_rule  # noqa: E402

OUT = ROOT / "outputs"
ALL_RULES = list(PRIORITY_RULES.keys())


def main() -> int:
    ap = argparse.ArgumentParser(description="RCPSP 启发式调度对比实验")
    ap.add_argument("--samples", type=int, default=400, help="随机多起点搜索样本数")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--no-search", action="store_true", help="跳过随机多起点搜索")
    ap.add_argument("--capacity-role", default="FE", help="容量敏感性分析的角色")
    args = ap.parse_args()

    OUT.mkdir(exist_ok=True)
    plotting.setup_style()

    print("[1/6] 载入实例 ...")
    proj = load_project(ROOT / "data")
    cpm = compute_cpm(proj)
    lb = lower_bounds(proj, cpm)
    print("      任务 %d 个，角色 %d 个，依赖边 %d 条"
          % (len(proj), len(proj.resources),
             sum(len(t.predecessors) for t in proj.tasks.values())))
    print("      CPM（无资源约束）工期 = %.0f 天" % cpm.project_duration)

    tasks = []
    for t in proj.task_list():
        tasks.append({
            "task_id": t.task_id, "name": t.name, "phase": t.phase,
            "duration": t.duration,
            "predecessors": "|".join(t.predecessors),
            "roles": "+".join("%s%d" % (r, q) for r, q in sorted(proj.demands.get(t.task_id, {}).items())),
            "effort": t.duration * sum(proj.demands.get(t.task_id, {}).values()),
        })
    pd.DataFrame(tasks).to_csv(OUT / "tasks.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(cpm.rows()).to_csv(OUT / "cpm_schedule.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(rule_table()).to_csv(OUT / "rules.csv", index=False, encoding="utf-8-sig")

    print("[2/6] 识别无约束计划中的资源冲突 ...")
    conflict = resource_conflict_report(proj, cpm)
    pd.DataFrame([{k: v for k, v in r.items() if not k.startswith("_")}
                  for r in conflict]).to_csv(OUT / "conflict.csv", index=False, encoding="utf-8-sig")

    print("[3/6] 运行 8 种优先规则（串行 / 并行 SGS）...")
    serial_rows = compare_rules(proj, ALL_RULES, method="serial")
    parallel_rows = compare_rules(proj, ALL_RULES, method="parallel")
    improved_rows = compare_rules(proj, ALL_RULES, method="serial", improve=True)

    def dump(rows, name):
        pd.DataFrame([{k: v for k, v in r.items() if k != "schedule"}
                      for r in rows]).to_csv(OUT / name, index=False, encoding="utf-8-sig")

    dump(serial_rows, "rules_serial.csv")
    dump(parallel_rows, "rules_parallel.csv")
    dump(improved_rows, "rules_improved.csv")

    best = improved_rows[0]
    sch = best["schedule"]
    pd.DataFrame(sch.rows()).to_csv(OUT / "schedule_best.csv", index=False, encoding="utf-8-sig")

    util_table = resource_utilisation_table(proj, sch)
    pd.DataFrame(util_table).to_csv(OUT / "utilisation.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(phase_utilisation(proj, sch)).to_csv(OUT / "phase_usage.csv",
                                                      index=False, encoding="utf-8-sig")

    print("[4/6] 瓶颈角色容量敏感性 ...")
    role = args.capacity_role
    original = proj.resources[role]
    cap_rows = []
    max_demand = max((proj.demands.get(t, {}).get(role, 0) for t in proj.order), default=1)
    caps = sorted({max_demand, max_demand + 1, max_demand + 2, max_demand + 3,
                   max_demand + 4, max_demand + 6})
    try:
        for cap in caps:
            proj.resources[role] = Resource(original.role_id, original.role_name, int(cap))
            for rule in ("LFT", "LST", "MSLK", "SPT", "GRPW"):
                s = schedule_with_rule(proj, rule)
                s2, _ = double_justify(proj, s)
                cap_rows.append({
                    "role": role, "capacity": int(cap), "rule": rule,
                    "makespan": float(s2.makespan),
                    "mean_utilisation": float(sum(s2.utilisation.values()) / len(s2.utilisation)),
                    "role_utilisation": float(s2.utilisation[role]),
                })
    finally:
        proj.resources[role] = original
    pd.DataFrame(cap_rows).to_csv(OUT / "capacity.csv", index=False, encoding="utf-8-sig")

    search_info = {}
    if not args.no_search:
        print("[5/6] 随机键多起点搜索（%d 次）..." % args.samples)
        ms, hist = random_multistart(proj, n_samples=args.samples, seed=args.seed, improve=True)
        pd.DataFrame({"iteration": range(1, len(hist) + 1), "best": hist}).to_csv(
            OUT / "search.csv", index=False, encoding="utf-8-sig")
        search_info = {"best_makespan": float(ms.makespan), "samples": args.samples}
    else:
        print("[5/6] 已跳过随机多起点搜索")

    print("[6/6] 生成图表 ...")
    plotting.plot_phase_workload(proj)
    plotting.plot_network(proj, cpm)
    plotting.plot_conflict(proj, cpm)
    plotting.plot_rule_comparison(serial_rows, cpm.project_duration)
    plotting.plot_gantt(proj, sch,
                        title="最优规则方案甘特图（%s + 前向-后向改进，总工期 %d 天；红框=资源受限关键序列）"
                              % (sch.rule, sch.makespan))
    plotting.plot_resource_profile(proj, sch)
    plotting.plot_utilisation(util_table)
    plotting.plot_capacity(cap_rows, role)
    plotting.plot_improvement(serial_rows, improved_rows)

    m_serial = {r["rule"]: float(r["makespan"]) for r in serial_rows}
    m_par = {r["rule"]: float(r["makespan"]) for r in parallel_rows}
    m_imp = {r["rule"]: float(r["makespan"]) for r in improved_rows}
    best_rule = min(m_imp, key=m_imp.get)
    worst_rule = max(m_imp, key=m_imp.get)

    lines = [
        "# 实验结果摘要",
        "",
        "实例：%s　任务 %d 个，角色 %d 个" % (proj.name, len(proj), len(proj.resources)),
        "",
        "## 基准",
        "- 无资源约束（CPM）总工期：**%.0f 天**" % cpm.project_duration,
        "- 资源工作量下界：%.1f 天（瓶颈角色 %s）" % (lb["work_content"], lb["binding_resource"]),
        "- 关键路径：%s" % " → ".join(cpm.critical_path),
        "",
        "## 资源冲突（无约束计划）",
    ]
    for r in conflict:
        if r["has_conflict"]:
            lines.append("- %s（%s）：容量 %d，峰值需求 %d，超载 %d 天，最大超载 %d 人"
                         % (r["role_id"], r["role_name"], r["capacity"], r["peak_demand"],
                            r["conflict_days"], r["max_overload"]))
    lines += [
        "",
        "## 优先规则对比（单位：天）",
        "",
        "| 规则 | 串行 SGS | 并行 SGS | 串行+改进 | 相对 CPM |",
        "| --- | --- | --- | --- | --- |",
    ]
    for rule in ALL_RULES:
        lines.append("| %s | %d | %d | %d | +%d |"
                     % (rule, m_serial[rule], m_par[rule], m_imp[rule],
                        m_imp[rule] - cpm.project_duration))
    lines += [
        "",
        "- 最优规则：**%s**，总工期 %d 天（较 CPM 增加 %d 天，+%.1f%%）"
        % (best_rule, m_imp[best_rule], m_imp[best_rule] - cpm.project_duration,
           100 * (m_imp[best_rule] - cpm.project_duration) / cpm.project_duration),
        "- 最差规则：**%s**，总工期 %d 天；规则极差 %d 天（%.1f%%）"
        % (worst_rule, m_imp[worst_rule], m_imp[worst_rule] - m_imp[best_rule],
           100 * (m_imp[worst_rule] - m_imp[best_rule]) / m_imp[best_rule]),
        "- 并行 SGS 相对串行 SGS 的平均差：%+.1f 天"
        % (sum(m_par[r] - m_serial[r] for r in ALL_RULES) / len(ALL_RULES)),
        "",
        "## 前向—后向改进效果",
        "- 平均缩短 %.2f 天（%.2f%%），%d/%d 条规则得到改进"
        % (sum(m_serial[r] - m_imp[r] for r in ALL_RULES) / len(ALL_RULES),
           100 * sum(m_serial[r] - m_imp[r] for r in ALL_RULES) / sum(m_serial[r] for r in ALL_RULES),
           sum(1 for r in ALL_RULES if m_imp[r] < m_serial[r]), len(ALL_RULES)),
        "",
        "## 资源利用率（最优方案）",
        "",
        "| 角色 | 容量 | 忙碌人日 | 利用率 | 峰值占用 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in util_table:
        lines.append("| %s | %d | %.1f | %.1f%% | %d |"
                     % (r["role_id"], r["capacity"], r["busy_person_days"],
                        100 * r["utilisation"], r["peak_usage"]))
    lines += ["", "## 随机多起点搜索"]
    if search_info:
        lines.append("- %d 次随机优先序列 + 改进，最好解 **%d 天**"
                     % (search_info["samples"], search_info["best_makespan"]))
        lines.append("- 最优规则解与之相差 %+d 天（%+.1f%%）"
                     % (m_imp[best_rule] - search_info["best_makespan"],
                        100 * (m_imp[best_rule] - search_info["best_makespan"])
                        / search_info["best_makespan"]))
    else:
        lines.append("- 本次运行已跳过")
    (OUT / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    meta = {
        "project": proj.name, "tasks": len(proj), "roles": len(proj.resources),
        "cpm": cpm.project_duration, "best_rule": best_rule,
        "best_makespan": float(m_imp[best_rule]),
        "serial": m_serial, "parallel": m_par, "improved": m_imp,
        "conflict": [{k: v for k, v in r.items() if not k.startswith("_")} for r in conflict],
        "utilisation": util_table, "search": search_info,
    }
    (OUT / "summary.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False),
                                      encoding="utf-8")
    print("\n完成：结果在 outputs/，图表在 docs/figures/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
