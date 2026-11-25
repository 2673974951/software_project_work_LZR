"""命令行入口：不写代码也能完成题目 3 的全部实验.

用法::

    python -m rcpsp info                     # 查看实例与资源冲突
    python -m rcpsp cpm                      # 无资源约束的关键路径
    python -m rcpsp schedule --rule LFT      # 按指定规则排一次
    python -m rcpsp compare                  # 8 种规则对比
    python -m rcpsp compare --improve        # 叠加前向—后向改进
    python -m rcpsp capacity --role FE       # 前端人力敏感性
    python -m rcpsp all                      # 复现全部图表与结果表
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from .cpm import compute_cpm
from .metrics import (
    lower_bounds,
    resource_conflict_report,
    resource_utilisation_table,
)
from .project import load_project
from .rules import PRIORITY_RULES
from .sgs import double_justify, schedule_with_rule

ROOT = Path(__file__).resolve().parents[2]


def _load(args):
    return load_project(args.data, name=args.name)


def cmd_info(args) -> int:
    proj = _load(args)
    cpm = compute_cpm(proj)
    lb = lower_bounds(proj, cpm)
    print("实例：%s" % proj.name)
    print("任务 %d 个，角色 %d 个，依赖边 %d 条"
          % (len(proj), len(proj.resources),
             sum(len(t.predecessors) for t in proj.tasks.values())))
    print("无资源约束（CPM）总工期：%.0f 天" % cpm.project_duration)
    print("资源工作量下界：%.1f 天（瓶颈角色 %s）" % (lb["work_content"], lb["binding_resource"]))
    print()
    print("角色容量：")
    for r in sorted(proj.resources):
        res = proj.resources[r]
        print("  %-5s %-12s 容量 %d" % (res.role_id, res.role_name, res.capacity))
    print()
    print("资源冲突（按依赖尽早开工时的超额需求）：")
    any_conflict = False
    for row in resource_conflict_report(proj, cpm):
        if row["has_conflict"]:
            any_conflict = True
            print("  %-5s 峰值 %2d / 容量 %d，超载 %2d 天，最大超载 %d 人"
                  % (row["role_id"], row["peak_demand"], row["capacity"],
                     row["conflict_days"], row["max_overload"]))
    if not any_conflict:
        print("  未发现资源冲突，实例退化为普通 CPM")
    return 0


def cmd_cpm(args) -> int:
    proj = _load(args)
    cpm = compute_cpm(proj)
    print("无资源约束总工期：%.0f 天" % cpm.project_duration)
    print("关键路径：%s" % " -> ".join(cpm.critical_path))
    print()
    print("%-6s %6s %6s %6s %6s %6s %8s" % ("任务", "工期", "ES", "EF", "LS", "LF", "总时差"))
    for i, tid in enumerate(cpm.task_ids):
        print("%-6s %6.0f %6.0f %6.0f %6.0f %6.0f %8.0f%s"
              % (tid, cpm.durations[i], cpm.es[i], cpm.ef[i], cpm.ls[i], cpm.lf[i],
                 cpm.total_float[i], "  *" if cpm.total_float[i] <= 1e-9 else ""))
    return 0


def cmd_schedule(args) -> int:
    proj = _load(args)
    method = "parallel" if args.parallel else "serial"
    sch = schedule_with_rule(proj, args.rule, method=method)
    if args.improve:
        sch, hist = double_justify(proj, sch)
        print("前向-后向改进：%s" % " → ".join(str(h) for h in hist))
    cpm = compute_cpm(proj)
    print("规则 %s（%s），生成机制 %s" % (args.rule, PRIORITY_RULES[args.rule].name, sch.method))
    print("总工期 %d 天（无资源约束 %.0f 天，资源等待 +%d 天）"
          % (sch.makespan, cpm.project_duration, sch.makespan - int(cpm.project_duration)))
    print()
    print("%-6s %8s %8s %8s" % ("任务", "开工", "完工", "工期"))
    for row in sch.rows():
        print("%-6s %8d %8d %8d" % (row["task_id"], row["start"], row["finish"], row["duration"]))
    print()
    print("资源利用率：")
    for row in resource_utilisation_table(proj, sch):
        print("  %-5s %5.1f%%（峰值 %d / 容量 %d）"
              % (row["role_id"], 100 * row["utilisation"], row["peak_usage"], row["capacity"]))
    return 0


def cmd_compare(args) -> int:
    from .metrics import compare_rules
    proj = _load(args)
    cpm = compute_cpm(proj)
    rules = list(PRIORITY_RULES)
    if args.method in ("both", "serial"):
        rows = compare_rules(proj, rules, method="serial", improve=args.improve)
        print("串行 SGS%s：" % (" + 前向-后向改进" if args.improve else ""))
        print("%-8s %-22s %8s %8s %10s" % ("规则", "名称", "总工期", "相对 CPM", "平均利用率"))
        for r in rows:
            print("%-8s %-22s %8d %+8d %9.1f%%"
                  % (r["rule"], PRIORITY_RULES[r["rule"]].name, r["makespan"],
                     r["makespan"] - int(cpm.project_duration), 100 * r["mean_utilisation"]))
    if args.method in ("both", "parallel"):
        rows = compare_rules(proj, rules, method="parallel", improve=False)
        print()
        print("并行 SGS：")
        print("%-8s %-22s %8s %8s" % ("规则", "名称", "总工期", "相对 CPM"))
        for r in rows:
            print("%-8s %-22s %8d %+8d"
                  % (r["rule"], PRIORITY_RULES[r["rule"]].name, r["makespan"],
                     r["makespan"] - int(cpm.project_duration)))
    return 0


def cmd_capacity(args) -> int:
    from .metrics import compare_rules
    from .project import Resource
    proj = _load(args)
    role = args.role
    original = proj.resources[role]
    max_demand = max((proj.demands.get(t, {}).get(role, 0) for t in proj.order), default=1)
    print("角色 %s（%s）容量敏感性：" % (role, original.role_name))
    print("%8s %10s %10s" % ("容量", "最优工期", "该角色利用率"))
    try:
        for cap in range(max_demand, max_demand + 5):
            proj.resources[role] = Resource(original.role_id, original.role_name, cap)
            rows = compare_rules(proj, ["LFT", "LST", "GRPW"], method="serial", improve=True)
            best = rows[0]
            print("%8d %10d %9.1f%%"
                  % (cap, best["makespan"], 100 * best["schedule"].utilisation[role]))
    finally:
        proj.resources[role] = original
    return 0


def cmd_all(args) -> int:
    script = ROOT / "scripts" / "run_experiment.py"
    if not script.exists():
        print("找不到脚本：%s" % script, file=sys.stderr)
        return 2
    return subprocess.call([sys.executable, str(script)])


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rcpsp", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", default=str(ROOT / "data"), help="数据目录")
    p.add_argument("--name", default="智慧校园综合服务平台 v2.0")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("info", help="实例概况与资源冲突识别")
    sp.set_defaults(func=cmd_info)

    sp = sub.add_parser("cpm", help="无资源约束的关键路径")
    sp.set_defaults(func=cmd_cpm)

    sp = sub.add_parser("schedule", help="按指定规则排一次")
    sp.add_argument("--rule", default="LFT", choices=list(PRIORITY_RULES))
    sp.add_argument("--parallel", action="store_true", help="使用并行 SGS")
    sp.add_argument("--improve", action="store_true", help="叠加前向-后向改进")
    sp.set_defaults(func=cmd_schedule)

    sp = sub.add_parser("compare", help="多种规则对比")
    sp.add_argument("--method", default="both", choices=["serial", "parallel", "both"])
    sp.add_argument("--improve", action="store_true")
    sp.set_defaults(func=cmd_compare)

    sp = sub.add_parser("capacity", help="某角色人力容量敏感性")
    sp.add_argument("--role", default="FE")
    sp.set_defaults(func=cmd_capacity)

    sp = sub.add_parser("all", help="复现全部图表与结果表")
    sp.set_defaults(func=cmd_all)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
