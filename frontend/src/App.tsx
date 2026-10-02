import { Link, Outlet, useLocation } from "react-router-dom";

export default function App() {
  const { pathname } = useLocation();
  const link = (active: boolean) =>
    `text-xs transition-colors duration-fast ${
      active ? "text-accent" : "text-secondary hover:text-primary"
    }`;

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-30 border-b border-edge bg-panel/80 backdrop-blur">
        <div className="mx-auto flex max-w-[1800px] items-center gap-6 px-5 py-3">
          <Link to="/" className="text-sm font-semibold tracking-tight text-primary">
            TradeArena<span className="text-accent">X</span>
          </Link>
          <nav className="flex gap-4">
            <Link to="/" className={link(pathname === "/")}>
              Simulations
            </Link>
            <Link to="/new" className={link(pathname === "/new")}>
              New run
            </Link>
          </nav>
          {/* The authenticity disclosure lives in the chrome, not in a footer.
              Anyone looking at a PnL number here should be able to see what
              produced it without scrolling. */}
          <span className="ml-auto hidden text-2xs text-muted md:block">
            Synthetic GBM price path · strategy research tool · no live trading
          </span>
        </div>
      </header>

      <main className="mx-auto max-w-[1800px] px-5 py-5">
        <Outlet />
      </main>

      <footer className="border-t border-edge px-5 py-5 text-2xs text-muted">
        <div className="mx-auto max-w-[1800px] space-y-1">
          <p>
            Real: price-time-priority matching, agent logic, FIFO PnL accounting,
            Sharpe / drawdown / inventory risk, exact book replay. Simulated: the
            price process itself (geometric Brownian motion with jump shocks).
          </p>
          <p>
            No live market data feed, no broker integration, and no capital at risk.
            See <span className="num">GET /meta/engine-config</span> for every formula
            in force.
          </p>
        </div>
      </footer>
    </div>
  );
}
