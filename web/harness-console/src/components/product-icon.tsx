export type ProductIconName =
  | "agent"
  | "api"
  | "appearance"
  | "archive"
  | "book"
  | "clock"
  | "data"
  | "logout"
  | "members"
  | "memory"
  | "profile"
  | "security"
  | "session"
  | "settings";

export function ProductIcon({
  name,
  className,
}: {
  name: ProductIconName;
  className?: string;
}) {
  const drawing = (() => {
    switch (name) {
      case "agent":
        return <><circle cx="6" cy="6" r="2" /><circle cx="14" cy="6" r="2" /><circle cx="10" cy="14" r="2" /><path d="m7.7 7.1 1.4 4.8m3.2-4.8-1.4 4.8M8 6h4" /></>;
      case "api":
        return <><path d="M7.5 4 4 7.5 7.5 11m5-7L16 7.5 12.5 11M11.5 2.8 8.5 13" /><path d="M4 15.5h12" /></>;
      case "appearance":
        return <><path d="M10 3.5a6.5 6.5 0 1 0 0 13c1.2 0 1.7-.7 1.2-1.6-.5-.9.2-1.8 1.2-1.8h1.1a3 3 0 0 0 3-3A6.5 6.5 0 0 0 10 3.5Z" /><circle cx="6.8" cy="8" r=".7" /><circle cx="9.3" cy="5.9" r=".7" /><circle cx="12.3" cy="6.5" r=".7" /></>;
      case "archive":
        return <><path d="M3.5 7h13v8a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 15Z" /><path d="M2.8 3.5h14.4V7H2.8ZM7.5 10h5" /></>;
      case "book":
        return <><path d="M4 4.5h6a2 2 0 0 1 2 2v10H6a2 2 0 0 1-2-2Zm12 0h-4a2 2 0 0 0-2 2" /><path d="M16 4.5v10a2 2 0 0 0-2-2h-2" /></>;
      case "clock":
        return <><circle cx="10" cy="10" r="6.5" /><path d="M10 6.5v4l2.8 1.7" /></>;
      case "data":
        return <><ellipse cx="10" cy="5" rx="6" ry="2.5" /><path d="M4 5v5c0 1.4 2.7 2.5 6 2.5s6-1.1 6-2.5V5m-12 5v5c0 1.4 2.7 2.5 6 2.5s6-1.1 6-2.5v-5" /></>;
      case "logout":
        return <><path d="M8 4H5a1.5 1.5 0 0 0-1.5 1.5v9A1.5 1.5 0 0 0 5 16h3" /><path d="M12.5 6.5 16 10l-3.5 3.5M8 10h8" /></>;
      case "members":
        return <><circle cx="7" cy="7" r="2.5" /><circle cx="14" cy="8" r="2" /><path d="M2.8 16c.5-3 2-4.5 4.3-4.5s3.8 1.5 4.3 4.5m.1-3.5c.7-.8 1.5-1.2 2.5-1.2 1.8 0 3 1.2 3.4 3.5" /></>;
      case "memory":
        return <><path d="M7 4.5a2.5 2.5 0 0 0-2.2 3.7A2.7 2.7 0 0 0 5.5 13a2.5 2.5 0 0 0 4.5 1.5v-9A2.5 2.5 0 0 0 7 4.5Z" /><path d="M13 4.5a2.5 2.5 0 0 1 2.2 3.7 2.7 2.7 0 0 1-.7 4.8 2.5 2.5 0 0 1-4.5 1.5m-4.7-6h2.2m5-1h2.2M6 13h2m4 0h2" /></>;
      case "profile":
        return <><circle cx="10" cy="7" r="3" /><path d="M4.5 16c.7-3.4 2.5-5 5.5-5s4.8 1.6 5.5 5" /></>;
      case "security":
        return <><path d="m10 3 6 2.3v4.5c0 3.8-2.3 6.2-6 7.7-3.7-1.5-6-3.9-6-7.7V5.3Z" /><path d="m7.5 10 1.7 1.7 3.5-4" /></>;
      case "session":
        return <><rect x="3.5" y="4" width="13" height="12" rx="2" /><path d="M3.5 7.5h13M6 5.8h.1m2 0h.1M7.5 12h5" /></>;
      case "settings":
        return <><circle cx="10" cy="10" r="2.7" /><path d="M8.4 3.7 9 2.5h2l.6 1.2 1.4.6 1.3-.4 1.4 1.4-.4 1.3.6 1.4 1.2.6v2l-1.2.6-.6 1.4.4 1.3-1.4 1.4-1.3-.4-1.4.6-.6 1.2H9l-.6-1.2-1.4-.6-1.3.4-1.4-1.4.4-1.3-.6-1.4-1.2-.6v-2L4.1 8l.6-1.4-.4-1.3 1.4-1.4 1.3.4Z" /></>;
    }
  })();

  return (
    <svg
      className={className}
      data-product-icon={name}
      viewBox="0 0 20 20"
      aria-hidden="true"
    >
      {drawing}
    </svg>
  );
}
