import { Component, type ReactNode } from "react";
import { AlertTriangle } from "lucide-react";

type Props = { children: ReactNode };
type State = { error: Error | null };

/**
 * 全站渲染错误兜底：任何视图在渲染期抛错时显示可恢复的错误卡，
 * 而不是整棵 React 树卸载后的白屏（生产构建没有 Vite 错误浮层）。
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error) {
    // 保留控制台痕迹便于排障；展示层不暴露堆栈。
    console.error("[采价台] 页面渲染出错：", error);
  }

  render() {
    if (this.state.error) {
      return (
        <main className="proc-gate" role="alert">
          <span><AlertTriangle size={22} /></span>
          <h1>页面渲染出错</h1>
          <p>{this.state.error.message || "发生了未知错误，数据不会因此丢失。"}</p>
          <div className="flex gap-2">
            <button
              type="button"
              className="mt-4 px-4 py-2 rounded-lg bg-accent text-white text-sm font-semibold hover:bg-accent-hover transition-colors"
              onClick={() => this.setState({ error: null })}
            >
              尝试恢复
            </button>
            <button
              type="button"
              className="mt-4 px-4 py-2 rounded-lg border border-border bg-surface text-text text-sm font-medium hover:bg-surface-subtle transition-colors"
              onClick={() => window.location.reload()}
            >
              刷新页面
            </button>
          </div>
        </main>
      );
    }
    return this.props.children;
  }
}
