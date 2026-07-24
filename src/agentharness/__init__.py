"""Agent Harness — extensible Python agent runtime with readonly React console."""

from agentharness.contracts import (
    ContextBundle,
    ContextManifest,
    ConversationTurn,
    EventEnvelope,
    Message,
    ModelStreamItem,
    ModelTurn,
    RunRequest,
    RunResult,
    ToolCall,
    ToolResult,
    ToolSpec,
    Usage,
    VerificationDecision,
    VerificationPolicy,
)
from agentharness.eval.calibration import CalibrationDataset, JudgeCalibrator
from agentharness.eval.contracts import (
    AgentTrace,
    CalibrationReport,
    CheckResult,
    DiagnosisReport,
    EvaluationPolicy,
    EvaluationReport,
    EvidenceRef,
    GateDecision,
    ReplaySnapshot,
    TraceSpan,
    TraceVersions,
)
from agentharness.eval.diagnosis import DiagnosisEngine
from agentharness.eval.regression import RegressionGate
from agentharness.eval.replay import OfflineReplay, SnapshotStore
from agentharness.eval.trusted_judge import JudgeOrchestrator
from agentharness.harness import Harness

__all__ = [
    "Harness",
    "OfflineReplay",
    "SnapshotStore",
    "AgentTrace",
    "TraceSpan",
    "TraceVersions",
    "EvaluationPolicy",
    "CheckResult",
    "EvidenceRef",
    "EvaluationReport",
    "DiagnosisReport",
    "DiagnosisEngine",
    "ReplaySnapshot",
    "CalibrationReport",
    "CalibrationDataset",
    "GateDecision",
    "RegressionGate",
    "JudgeCalibrator",
    "JudgeOrchestrator",
    "RunRequest",
    "RunResult",
    "ConversationTurn",
    "ContextBundle",
    "ContextManifest",
    "Message",
    "ModelTurn",
    "ModelStreamItem",
    "ToolCall",
    "ToolResult",
    "Usage",
    "ToolSpec",
    "EventEnvelope",
    "VerificationPolicy",
    "VerificationDecision",
]

__version__ = "0.1.0"
