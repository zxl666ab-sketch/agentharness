import { Component, type ErrorInfo, type ReactNode } from "react";
import { AlertTriangle } from "lucide-react";

type Props = { children: ReactNode };
type State = { failed: boolean };

export class AppErrorBoundary extends Component<Props, State> {
  state: State = { failed: false };

  static getDerivedStateFromError(): State {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Uncaught procurement UI error", error, info.componentStack);
  }

  render() {
    if (this.state.failed) {
      return (
        <main className="proc-gate" role="alert">
          <span><AlertTriangle size={22} /></span>
          <h1>页面显示失败</h1>
          <p>请刷新页面；采购数据仍保存在本地服务中。</p>
        </main>
      );
    }
    return this.props.children;
  }
}
