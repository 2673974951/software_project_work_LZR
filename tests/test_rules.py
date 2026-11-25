"""rules 模块单元测试."""
import numpy as np
import pytest

from rcpsp.project import Project, Task
from rcpsp.rules import (
    PRIORITY_RULES,
    priority_values,
    rank_positional_weight,
    transitive_successor_count,
)


def build(spec):
    tasks = {k: Task(k, k, "测试", d, tuple(p)) for k, (d, p) in spec.items()}
    return Project("t", tasks)


def test_all_rules_produce_finite_values():
    proj = build({"A": (2, []), "B": (3, ["A"]), "C": (1, ["A"]), "D": (2, ["B", "C"])})
    for rule in PRIORITY_RULES:
        v = priority_values(proj, rule, rng=np.random.default_rng(0))
        assert v.shape == (4,)
        assert np.all(np.isfinite(v))


def test_unknown_rule_raises():
    proj = build({"A": (1, [])})
    with pytest.raises(ValueError):
        priority_values(proj, "NOPE")


def test_transitive_successor_count():
    proj = build({"A": (1, []), "B": (1, ["A"]), "C": (1, ["B"])})
    cnt = transitive_successor_count(proj)
    # A 的后继是 B、C；B 的后继是 C；C 没有后继
    assert cnt[proj.index["A"]] == 2
    assert cnt[proj.index["B"]] == 1
    assert cnt[proj.index["C"]] == 0


def test_rank_positional_weight_is_path_length():
    proj = build({"A": (2, []), "B": (3, ["A"]), "C": (1, ["B"])})
    w = rank_positional_weight(proj, proj.durations())
    assert w[proj.index["A"]] == pytest.approx(6.0)
    assert w[proj.index["B"]] == pytest.approx(4.0)
    assert w[proj.index["C"]] == pytest.approx(1.0)


def test_spt_orders_by_duration():
    proj = build({"A": (5, []), "B": (1, []), "C": (3, [])})
    v = priority_values(proj, "SPT")
    order = [proj.order[i] for i in np.argsort(v)]
    assert order == ["B", "C", "A"]


def test_lft_puts_critical_first():
    proj = build({"A": (5, []), "B": (1, []), "C": (3, [])})
    v = priority_values(proj, "LFT")
    # 关键任务 A 的最晚完成时间最小，应排在最前
    assert proj.order[int(np.argmin(v))] == "A"
