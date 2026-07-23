import {
  CheckCircle2,
  CircleAlert,
  Inbox,
  LoaderCircle,
  Radio,
} from "lucide-react";

export type PanelStateKind = "loading" | "empty" | "error" | "streaming" | "done";

type Props = {
  kind: PanelStateKind;
  title: string;
  detail?: string;
  compact?: boolean;
};

const ICONS = {
  loading: LoaderCircle,
  empty: Inbox,
  error: CircleAlert,
  streaming: Radio,
  done: CheckCircle2,
};

export function PanelState({ kind, title, detail, compact = false }: Props) {
  const Icon = ICONS[kind];
  return (
    <div
      className={`panel-state ${kind} ${compact ? "compact" : ""}`}
      role={kind === "error" ? "alert" : "status"}
      aria-live={kind === "error" ? "assertive" : "polite"}
    >
      <Icon size={compact ? 13 : 18} aria-hidden="true" />
      <span>
        <strong>{title}</strong>
        {detail && <small>{detail}</small>}
      </span>
    </div>
  );
}
