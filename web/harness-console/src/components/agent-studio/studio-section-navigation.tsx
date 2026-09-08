import Link from "next/link";
import styles from "./studio-section-navigation.module.css";

export type StudioSection = "skills" | "mcp";

const items: Array<{ id: StudioSection; href: string; label: string }> = [
  { id: "skills", href: "/studio/skills", label: "技能" },
  { id: "mcp", href: "/studio/capabilities", label: "MCP" },
];

export function StudioSectionNavigation({ active }: { active: StudioSection }) {
  return (
    <header className={styles.bar}>
      <nav className={styles.navigation} aria-label="技能与 MCP 管理">
        {items.map((item) => (
          <Link
            key={item.id}
            href={item.href}
            className={item.id === active ? styles.active : styles.link}
            aria-current={item.id === active ? "page" : undefined}
          >
            {item.label}
          </Link>
        ))}
      </nav>
    </header>
  );
}
