"""Eval package: offline suite runner, graders, baseline, reports."""

from agentharness.eval.baseline import (
    BaselineGates,
    RegressionReport,
    compare_to_baseline,
    load_baseline,
)
from agentharness.eval.dataset import (
    AssertionSpec,
    EvalCase,
    EvalConfigError,
    EvalSuite,
    SuiteDefaults,
    load_suite,
)
from agentharness.eval.graders import (
    CompositeGrader,
    DeterministicGrader,
    GradeResult,
    JudgeAdapter,
    JudgeVerdict,
    LLMJudgeGrader,
    Trajectory,
    TrajectoryGrader,
)
from agentharness.eval.report import (
    SCHEMA_VERSION,
    suite_report_to_dict,
    write_json_report,
    write_junit_xml,
)
from agentharness.eval.runner import CaseResult, GroupMetrics, SuiteReport, run_suite

__all__ = [
    "AssertionSpec",
    "BaselineGates",
    "CaseResult",
    "CompositeGrader",
    "DeterministicGrader",
    "EvalCase",
    "EvalConfigError",
    "EvalSuite",
    "GradeResult",
    "GroupMetrics",
    "JudgeAdapter",
    "JudgeVerdict",
    "LLMJudgeGrader",
    "RegressionReport",
    "SCHEMA_VERSION",
    "SuiteDefaults",
    "SuiteReport",
    "Trajectory",
    "TrajectoryGrader",
    "compare_to_baseline",
    "load_baseline",
    "load_suite",
    "run_suite",
    "suite_report_to_dict",
    "write_json_report",
    "write_junit_xml",
]
