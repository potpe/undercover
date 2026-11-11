export type View = "queue" | "scoreboard" | "configuration" | "settings";

const pages: { view: View; label: string }[] = [
  { view: "queue", label: "Claims" },
  { view: "scoreboard", label: "Scoreboard" },
  { view: "configuration", label: "Configuration" },
  { view: "settings", label: "Settings" },
];

export function Navigation({
  view,
  onSelect,
}: Readonly<{
  view: View;
  onSelect: (view: View) => void;
}>) {
  return (
    <nav aria-label="Main navigation">
      {pages.map((page) => (
        <button
          key={page.view}
          type="button"
          className={view === page.view ? "nav-active" : ""}
          aria-current={view === page.view ? "page" : undefined}
          onClick={() => {
            onSelect(page.view);
          }}
        >
          {page.label}
        </button>
      ))}
    </nav>
  );
}
