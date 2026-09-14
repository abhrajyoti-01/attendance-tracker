import { Component } from "react";
import type { ErrorInfo, ReactNode } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

/** Prevents a single render error from white-screening the whole app. */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("Unhandled render error", error, info.componentStack);
  }

  private reset = () => {
    this.setState({ error: null });
  };

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    return (
      <div className="boundary">
        <div className="boundary-card">
          <h1>Something went wrong</h1>
          <p className="muted">
            The page hit an unexpected error. Reloading usually fixes it; if it keeps happening,
            contact your administrator.
          </p>
          <pre className="boundary-detail">{error.message}</pre>
          <div className="boundary-actions">
            <button type="button" className="btn btn-primary" onClick={this.reset}>
              Try again
            </button>
            <button
              type="button"
              className="btn"
              onClick={() => window.location.assign("/")}
            >
              Go to dashboard
            </button>
          </div>
        </div>
      </div>
    );
  }
}
