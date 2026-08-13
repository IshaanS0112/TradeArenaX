import { Link, Outlet, useLocation } from "react-router-dom";

export default function App() {
  const { pathname } = useLocation();

  return (
    <div className="min-h-screen">
      <header className="border-b border-edge bg-panel/60 backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center gap-6 px-6 py-4">
          <Link to="/" className="text-lg font-semibold tracking-tight text-slate-100">
            TradeArena<span className="text-accent">X</span>
          </Link>
          <nav className="flex gap-4 text-sm">
            <Link
              to="/"
              className={pathname === "/" ? "text-accent" : "text-muted hover:text-slate-200"}
            >
              Simulations
            </Link>
            <Link
              to="/new"
              className={
                pathname === "/new" ? "text-accent" : "text-muted hover:text-slate-200"
              }
            >
              New run
            </Link>
          </nav>
          {/* The authenticity disclosure lives in the chrome, not buried in a
              footer. Anyone looking at a PnL number on this dashboard should be
              able to see what produced it without scrolling. */}
          <span className="ml-auto hidden text-xs text-muted md:block">
            Synthetic GBM price data · strategy research tool · no live trading
          </span>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-6 py-8">
        <Outlet />
      </main>

      <footer className="border-t border-edge px-6 py-6 text-xs text-muted">
        <div className="mx-auto max-w-7xl space-y-1">
          <p>
            Real: price-time-priority matching, agent logic, FIFO PnL accounting,
            Sharpe / drawdown / inventory risk. Simulated: the price process itself
            (geometric Brownian motion with configurable jump shocks).
          </p>
          <p>
            No live market data feed, no broker integration, and no capital at risk.
            See <span className="font-mono">GET /meta/engine-config</span> for every
            formula in force.
          </p>
        </div>
      </footer>
    </div>
  );
}
